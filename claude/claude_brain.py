"""Claude decision brain — drives the `claude` CLI in headless mode (`claude -p`),
using the user's Claude **subscription** (NOT an Anthropic API key). Given the scored
watchlist + market regime + our ENABLED strategies, Claude returns a structured
playbook: which coins to play and which enabled strategy fits each.

Why the CLI and not the `anthropic` SDK: the user has a Claude Pro/Max subscription,
not a pay-as-you-go API key. The `claude` CLI authenticates with that subscription, so
shelling out to it costs no API credits. The agent must run on a machine where `claude`
is installed and logged in (the same machine that runs the bot).

Fail-open: if the CLI is missing or errors, a deterministic fallback picks straight from
the watchlist scores, so the agent ALWAYS produces a usable playbook.
"""
import asyncio
import json
import re
import shutil
import subprocess
from typing import List, Optional

from config import settings

# --- strategy archetype classification (name substring -> (type, one-liner)) ---
_ARCH = [
    ("stochrsi_mr", "reversion", "Buy when Stochastic-RSI is oversold (mean-reversion)."),
    ("rsi_stack", "reversion", "Buy oversold flush confirmed by Stochastic %K."),
    ("vwap_reversion", "reversion", "Buy a flush stretched below VWAP."),
    ("range_extreme", "reversion", "Buy a flush stretched far below EMA20."),
    ("mtf_reversion", "reversion", "Buy oversold flush confirmed on the higher timeframe."),
    ("double_extreme", "reversion", "Buy a volume-confirmed extreme down-flush."),
    ("climax_reversion", "reversion", "Buy a volume-climax capitulation at the lows."),
    ("support_reversion", "reversion", "Buy a liquidation flush into support."),
    ("adaptive_percentile_reversion", "reversion", "Buy a self-calibrating top-percentile down move (fade)."),
    ("volume_confirmed_reversion", "reversion", "Buy an oversold flush confirmed by a volume spike."),
    ("cascade_filter", "reversion", "Buy after a filtered liquidation cascade exhausts."),
    ("dominant_burst", "reversion", "Fade a dominant 5x imbalance burst (snap-back)."),
    ("liq_reversion", "reversion", "Buy the oversold liquidation flush, sell the bounce."),
    ("capitulation", "reversion", "Buy a capitulation candle after a sharp flush."),
    ("donchian_breakout", "breakout", "Buy a Donchian channel breakout (upside momentum)."),
    ("trend_follow", "trend", "Buy a pullback to the trend in a confirmed uptrend."),
    ("xsect_momentum", "relative-strength", "Buy the strongest relative performers."),
    ("whale_flow", "flow", "Buy when whale netflow is strongly positive."),
    ("news_catalyst", "event", "Buy on a fresh listing / news catalyst."),
]


def _classify(name: str):
    n = name.lower()
    for key, typ, desc in _ARCH:
        if key in n:
            return typ, desc
    return "spot", "Long spot strategy."


def _spot_book() -> List[str]:
    """Enabled strategies that actually trade in spot (active minus spot-excluded)."""
    from strategies.registry import active_strategies
    excl = {s.strip() for s in (getattr(settings, "spot_excluded_strategies", "") or "").split(",") if s.strip()}
    return [s.name for s in active_strategies(settings) if s.name not in excl]


def _strategy_menu() -> List[dict]:
    out = []
    for n in _spot_book():
        typ, desc = _classify(n)
        out.append({"name": n, "type": typ, "desc": desc})
    return out


_SYSTEM = (
    "You are the trading brain for XORR, an autonomous SPOT-ONLY, LONG-ONLY crypto agent "
    "competing in a contest judged on total return with a HARD ~30% max-drawdown "
    "disqualification gate. Constraints you must respect: you can only BUY (go long) liquid "
    "BEP-20 majors on PancakeSwap — no shorting, no leverage, no perps. Your job: from a "
    "scored watchlist, pick the few highest-conviction LONG setups and assign each the "
    "best-fit strategy from the ENABLED menu (use the strategy 'type' to match the setup: "
    "reversion for oversold flushes near range lows, breakout/trend for upside thrust near "
    "range highs). Be selective and capital-preserving: in RISK_OFF or down-trending "
    "regimes, prefer mean-reversion (buying oversold flushes) and pick fewer names; never "
    "chase. It is fine to return zero picks if nothing is compelling. Do not use any tools; "
    "reason only from the data provided. Respond with ONLY a JSON object, no prose, no code "
    "fences."
)


def _build_user_prompt(watchlist: dict, max_picks: int) -> str:
    payload = {
        "regime": watchlist.get("regime"),
        "scanned": watchlist.get("scanned"),
        "watchlist": watchlist.get("ranked", []),
        "enabled_strategies": _strategy_menu(),
        "instructions": {
            "pick_count": f"0 to {max_picks}",
            "rules": [
                "LONG spot only — every pick is a BUY.",
                "strategy MUST be exactly one 'name' from enabled_strategies.",
                "conviction is 0.0-1.0.",
                "Match strategy.type to the setup (reversion vs breakout/trend).",
                "These are ALERTS, not market buys: set entry_price to the level you want to "
                "BUY at, and the bot waits for price to reach it. For reversion (type=reversion) "
                "entry_price is a DIP at or below the current price (buy the flush). For "
                "breakout/trend, entry_price is ABOVE current price (buy the break). Use the "
                "coin's price, range_pos and atr_pct to set a realistic level.",
                "invalidation_price is the level that KILLS the idea — below support for "
                "reversion, a failed-breakout level for breakouts. If price hits it first, no trade.",
                "Fewer, higher-quality picks beat many marginal ones; it is fine to return none.",
            ],
            "output_schema": {
                "market_view": "one sentence on the tape",
                "picks": [{"symbol": "TICKER", "strategy": "exact_menu_name",
                           "conviction": 0.0, "entry_price": 0.0, "invalidation_price": 0.0,
                           "reason": "short"}],
                "avoid": ["TICKER", "..."],
            },
        },
    }
    return ("Pick what to play from this scored watchlist. Return ONLY the JSON object "
            "described in instructions.output_schema.\n\n" + json.dumps(payload))


def _extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def _call_claude_sync(user_prompt: str) -> Optional[dict]:
    """Invoke `claude -p` headless (subscription auth). Returns the parsed decision dict."""
    bin_ = shutil.which(getattr(settings, "claude_cli_bin", "claude")) or getattr(settings, "claude_cli_bin", "claude")
    if not shutil.which(bin_) and not bin_.endswith("claude"):
        return None
    cmd = [
        bin_, "-p", user_prompt,
        "--output-format", "json",
        "--model", getattr(settings, "claude_model", "claude-opus-4-8"),
        "--system-prompt", _SYSTEM,
        "--disallowed-tools", "Bash", "Read", "Edit", "Write", "WebSearch", "WebFetch", "Task",
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=int(getattr(settings, "claude_timeout_sec", 120)))
    except Exception as e:
        print(f"[CLAUDE] CLI call failed: {e}")
        return None
    if p.returncode != 0:
        print(f"[CLAUDE] CLI exit {p.returncode}: {(p.stderr or '')[:200]}")
        return None
    try:
        env = json.loads(p.stdout)
    except Exception:
        return None
    if env.get("is_error"):
        print(f"[CLAUDE] error: {str(env.get('result'))[:200]}")
        return None
    return _extract_json(str(env.get("result", "")))


def _fallback_playbook(watchlist: dict, max_picks: int, min_conv: float) -> dict:
    """Deterministic pick straight from the scores when Claude is unavailable."""
    regime = watchlist.get("regime", "CHOP")
    menu = {m["name"]: m for m in _strategy_menu()}
    rev = next((n for n, m in menu.items() if m["type"] == "reversion"), None)
    brk = next((n for n, m in menu.items() if m["type"] == "breakout"), rev)
    down = regime in ("RISK_OFF", "TREND_DOWN")
    picks = []
    for r in watchlist.get("ranked", []):
        if len(picks) >= (3 if down else max_picks):
            break
        is_rev = r["reversion_score"] >= r["breakout_score"]
        if down and not is_rev:
            continue  # in a down tape only buy flushes
        strat = rev if is_rev else brk
        conv = r["reversion_score"] if is_rev else r["breakout_score"]
        if not strat or conv < min_conv:
            continue
        price = float(r.get("price", 0)) or 0.0
        if is_rev:
            entry = round(price * 0.985, 8)   # buy a ~1.5% dip into the flush
            invalid = round(price * 0.95, 8)  # bail if it breaks ~5% lower (falling knife)
        else:
            entry = round(price * 1.01, 8)    # buy a ~1% break up
            invalid = round(price * 0.985, 8) # failed breakout
        picks.append({"symbol": r["symbol"], "strategy": strat,
                      "conviction": round(conv, 2),
                      "entry_price": entry, "invalidation_price": invalid,
                      "reason": ("oversold flush" if is_rev else "upside breakout") +
                                f" (rsi {r['rsi']:.0f}, ret4h {r['ret_4h']:.1f}%, volx {r['vol_spike']:.1f})"})
    return {"market_view": f"deterministic fallback ({regime})", "picks": picks,
            "avoid": [], "source": "fallback"}


def _validate(pb: dict, watchlist: dict, max_picks: int, min_conv: float) -> dict:
    """Keep only well-formed picks on enabled strategies / scanned symbols."""
    menu = {m["name"] for m in _strategy_menu()}
    syms = {r["symbol"] for r in watchlist.get("ranked", [])}
    def _num(x):
        try:
            v = float(x)
            return v if v > 0 else None
        except Exception:
            return None

    clean = []
    for p in (pb.get("picks") or []):
        try:
            sym = str(p["symbol"]).upper()
            strat = str(p["strategy"])
            conv = float(p.get("conviction", 0))
        except Exception:
            continue
        if strat in menu and conv >= min_conv and len(clean) < max_picks:
            clean.append({"symbol": sym, "strategy": strat, "conviction": round(conv, 2),
                          "entry_price": _num(p.get("entry_price")),
                          "invalidation_price": _num(p.get("invalidation_price")),
                          "reason": str(p.get("reason", ""))[:200]})
    pb["picks"] = clean
    pb["avoid"] = [str(a).upper() for a in (pb.get("avoid") or [])][:20]
    pb.setdefault("source", "claude")
    return pb


async def decide_playbook(watchlist: dict) -> dict:
    """Ask Claude (subscription CLI) what to play; fall back to scores on any failure."""
    max_picks = int(getattr(settings, "watchlist_max_picks", 5))
    min_conv = float(getattr(settings, "claude_min_conviction", 0.55))
    prompt = _build_user_prompt(watchlist, max_picks)
    raw = await asyncio.to_thread(_call_claude_sync, prompt)
    if raw and isinstance(raw, dict) and "picks" in raw:
        return _validate(raw, watchlist, max_picks, min_conv)
    return _fallback_playbook(watchlist, max_picks, min_conv)
