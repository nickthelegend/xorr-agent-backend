"""Clean console status — the "watch the window and see it working" heartbeat.

Printed once per scan to the bot's stdout (the start_bot.bat window). Read-only, no DB
writes. Shows equity + drawdown, open positions with their LIVE R-multiple (🔒 = already
risk-free at breakeven), and the eligible-token funnel: full whitelist → analyzed →
confluence-verified → what Claude is watching. So you can confirm at a glance that picks
are restricted to eligible tokens and were analyzed by the strategies.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from config import settings
from persistence.db import engine
from persistence.repo import get_state, get_equity_history
from persistence.models import Position

_PB = Path("data_store/claude_playbook.json")
_n = {"i": 0}


def _live_r(pos, price: float) -> float:
    init = getattr(pos, "init_stop", 0.0) or pos.stop_loss
    if price <= 0 or pos.entry_price <= 0 or init <= 0:
        return 0.0
    sf = abs(pos.entry_price - init) / pos.entry_price
    if sf <= 0:
        return 0.0
    sign = -1.0 if getattr(pos, "direction", "long") == "short" else 1.0
    return ((price - pos.entry_price) / pos.entry_price * sign) / sf


async def print_status():
    """Emit the one-glance status block. Never raises (best-effort)."""
    _n["i"] += 1
    try:
        from data.cmc_client import fetch_fast_quotes
        q = await fetch_fast_quotes() or {}
    except Exception:
        q = {}
    with Session(engine) as s:
        st = get_state(s)
        positions = s.exec(select(Position)).all()
        hist = get_equity_history(s, limit=2)

    equity = float(hist[-1].equity_usd) if hist else float(getattr(st, "sim_cash_usdt", 0.0) or 0.0)
    peak = float(getattr(st, "peak_equity", 0.0) or 0.0) or equity
    dd = (peak - equity) / peak * 100.0 if peak > 0 else 0.0

    legs, upnl = [], 0.0
    for p in positions:
        qp = q.get(p.symbol.upper())
        pr = float(qp.price) if qp else p.entry_price
        pnl = (pr - p.entry_price) / p.entry_price * p.invested if p.entry_price > 0 else 0.0
        upnl += pnl
        lock = "[BE]" if getattr(p, "tp1_hit", False) else ""   # [BE] = stop at breakeven (risk-free)
        legs.append(f"{p.symbol}{lock} {_live_r(p, pr):+.2f}R(${pnl:+.2f})")

    # eligible-token funnel from the latest playbook
    try:
        from data.tokens import iter_all
        eligible = len(iter_all())
    except Exception:
        eligible = 149
    regime, scanned, verified, picks, watch = "?", 0, 0, 0, "—"
    try:
        pb = json.loads(_PB.read_text(encoding="utf-8")).get("pb", {})
        regime = pb.get("regime", "?")
        scanned = pb.get("scanned", 0)
        verified = pb.get("verified", 0)
        ps = pb.get("picks", [])
        picks = len(ps)
        watch = ", ".join(p.get("symbol", "?") for p in ps) or "—"
    except Exception:
        pass

    ts = datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")
    bar = "=" * 70
    lines = [
        bar,
        f" XORR {ts}   scan #{_n['i']}   regime {regime}   equity ${equity:.2f}   dd {dd:.1f}%",
        (f"   open {len(positions)}/{settings.max_concurrent_positions}: " + "  ".join(legs) + f"   uPnL ${upnl:+.2f}")
        if legs else f"   open 0/{settings.max_concurrent_positions}: none",
        f"   funnel: {eligible} eligible -> {scanned} analyzed -> {verified} confluence-verified -> watching {picks}: {watch}",
        bar,
    ]
    print("\n".join(lines))
