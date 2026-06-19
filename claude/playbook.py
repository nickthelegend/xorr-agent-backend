"""Playbook cache + refresh + signal conversion.

The watchlist agent + Claude brain produce a "playbook" (what to play + the strategy
for each). This module refreshes it on a cadence, caches it (in-memory + a JSON file so
it survives a restart within the TTL), and converts the picks into spot LONG Signal
objects the trading pipeline can execute. Claude is the decision-maker; the pipeline
force-enters Claude's picks (bypassing the weak Groq council).
"""
import json
import os
import time
from pathlib import Path
from typing import List, Optional

from config import settings
from core.types import Signal, MarketContext
from data.tokens import resolve

_CACHE_PATH = Path("data_store/claude_playbook.json")
_mem = {"pb": None, "ts": 0.0}


def _ttl_sec() -> float:
    # allow a little staleness past the refresh interval before the playbook expires
    return float(getattr(settings, "watchlist_interval_hours", 4.0)) * 3600.0 * 1.5


def _load_disk():
    try:
        if _CACHE_PATH.exists():
            with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d.get("pb"), float(d.get("ts", 0.0))
    except Exception:
        pass
    return None, 0.0


def get_playbook() -> Optional[dict]:
    """Current playbook if fresh (within TTL), else None."""
    pb, ts = _mem["pb"], _mem["ts"]
    if pb is None:
        pb, ts = _load_disk()
        _mem["pb"], _mem["ts"] = pb, ts
    if pb is None:
        return None
    if (time.time() - ts) > _ttl_sec():
        return None
    return pb


def _store(pb: dict):
    ts = time.time()
    _mem["pb"], _mem["ts"] = pb, ts
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"pb": pb, "ts": ts}, f)
    except Exception:
        pass


async def refresh_playbook() -> dict:
    """Scan -> score -> ask Claude -> cache. Returns the new playbook."""
    from claude.watchlist_agent import build_watchlist
    from claude.claude_brain import decide_playbook
    wl = await build_watchlist()
    pb = await decide_playbook(wl)
    pb["regime"] = wl.get("regime")
    pb["scanned"] = wl.get("scanned")
    _store(pb)
    return pb


def _sl_tp(strategy: str):
    s = strategy.lower()
    if "breakout" in s or "donchian" in s:
        return 3.0, 6.0   # breakout: wider target
    if "trend" in s:
        return 3.0, 6.0
    return 3.5, 5.0       # reversion default


def to_signals(ctx: MarketContext) -> List[Signal]:
    """Convert the current playbook's picks into spot LONG signals (or [] if stale)."""
    pb = get_playbook()
    if not pb:
        return []
    lev = 1.0
    out = []
    open_syms = {p.symbol.upper() for p in (ctx.open_positions or [])}
    for p in pb.get("picks", []):
        try:
            sym = str(p["symbol"]).upper()
            strat = str(p["strategy"])
            conf = float(p.get("conviction", 0))
        except Exception:
            continue
        if sym in open_syms:
            continue   # already holding it
        t = resolve(sym)
        if not t or not t.contract:
            continue   # not tradable on-chain
        sl, tp = _sl_tp(strat)
        out.append(Signal(
            symbol=sym, contract=t.contract, side="buy",
            confidence=max(0.05, min(0.97, conf)),
            stop_loss_pct=sl, take_profit_pct=tp, max_hold_min=480,
            rationale=f"[Claude pick · {strat}] {str(p.get('reason',''))[:160]}",
            strategy_name=f"claude:{strat}", direction="long", venue="spot", leverage=lev,
        ))
    return out


def triggered_signals(ctx: MarketContext) -> List[Signal]:
    """Tier 2: which of Claude's watched picks have their entry ALERT triggered right now.

    For each pick, check the live price vs the entry zone + invalidation level + the market
    regime; emit a spot-long signal only for the ones that just came into play. This is the
    deterministic 'price reached -> analyse trend -> trade by strength' step — no Claude call.
    """
    from claude.claude_brain import _classify
    pb = get_playbook()
    if not pb:
        return []
    regime = getattr(ctx, "regime", "CHOP")
    open_syms = {p.symbol.upper() for p in (ctx.open_positions or [])}
    out = []
    for p in pb.get("picks", []):
        try:
            sym = str(p["symbol"]).upper()
            strat = str(p["strategy"])
            conf = float(p.get("conviction", 0))
        except Exception:
            continue
        if sym in open_syms:
            continue
        q = (ctx.quotes or {}).get(sym)
        price = float(q.price) if (q and q.price) else None
        if price is None:
            # Claude may pick a coin CMC didn't quote this tick — use the live Binance WS
            # price, and inject it into the quote map so the downstream swap is sized right.
            try:
                from data import ws_feed
                wp = ws_feed.get_price(sym, max_age_sec=180.0)
                price = float(wp) if wp else None
            except Exception:
                price = None
            if price and ctx.quotes is not None:
                from core.types import Quote
                from datetime import datetime, timezone
                ctx.quotes[sym] = Quote(symbol=sym, price=price, pct_1h=0, pct_24h=0,
                                        volume_24h=0, market_cap=0,
                                        last_updated=datetime.now(timezone.utc))
        if not price:
            continue
        entry = p.get("entry_price")
        invalid = p.get("invalidation_price")
        typ, _ = _classify(strat)

        # invalidation: idea is dead, never trigger
        if invalid:
            if typ in ("breakout", "trend") and price <= invalid:
                continue
            if typ not in ("breakout", "trend") and price <= invalid:
                continue  # reversion: broke support -> falling knife, skip

        # trend gate at the trigger (the 'analyse the trend' step, deterministic)
        if typ in ("breakout", "trend") and regime in ("TREND_DOWN", "RISK_OFF"):
            continue  # don't chase breakouts into a downtrend

        # entry zone reached?
        buf = float(getattr(settings, "claude_trigger_buffer", 0.004))  # 0.4% tolerance
        if entry is None:
            triggered = True                          # no level set -> enter at market
        elif typ in ("breakout", "trend"):
            triggered = price >= entry * (1 - buf)    # broke up into the zone (small tolerance)
        else:
            triggered = price <= entry * (1 + buf)    # dipped into the buy zone (or at-market)
        if not triggered:
            continue

        t = resolve(sym)
        if not t or not t.contract:
            continue
        # Risk = Claude's invalidation: stop exactly where the idea is proven wrong (clamped
        # 1-8%), target ~1.6x the risk. Every loss is then small + pre-defined, not a generic %.
        if invalid and 0 < invalid < price:
            sl = max(1.0, min(8.0, (price - invalid) / price * 100.0))
            # Wide hard TP (~2.5R) — the monitor moves the stop to breakeven at +1R and
            # trails past +1.6R, so winners run; this just caps the rare moonshot.
            tp = round(sl * float(getattr(settings, "claude_tp_r_multiple", 2.5)), 2)
        else:
            sl, tp = _sl_tp(strat)
        # signal strength: conviction, nudged up when deeper into a reversion zone
        strength = conf
        if entry and typ not in ("breakout", "trend") and price < entry:
            strength = min(0.97, conf + min(0.15, (entry - price) / entry * 2.0))
        out.append(Signal(
            symbol=sym, contract=t.contract, side="buy",
            confidence=max(0.05, min(0.97, strength)),
            stop_loss_pct=sl, take_profit_pct=tp, max_hold_min=480,
            rationale=f"[Claude alert · {strat}] price {price:.6g} entered zone (entry={entry}); "
                      f"{str(p.get('reason',''))[:120]}",
            strategy_name=f"claude:{strat}", direction="long", venue="spot", leverage=1.0,
        ))
    return out
