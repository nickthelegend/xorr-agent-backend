import uuid
import json
import time
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from sqlmodel import Session
from config import settings
from persistence.repo import get_state, update_state, remove_position, add_trade, get_positions
from persistence.models import Position, Trade
from core.twak_executor import TwakExecutor


def write_kill_audit_log(reason: str, action: str = "KILL_SWITCH"):
    """Appends a kill/de-risk event to decisions.jsonl audit log."""
    log_entry = {
        "id": str(uuid.uuid4()),
        "t": datetime.now(timezone.utc).isoformat(),
        "symbol": "SYSTEM",
        "action": action,
        "strategy": "SYSTEM",
        "filters_passed": [],
        "filters_blocked": [],
        "brain_score": 0.0,
        "reasoning": reason,
        "market_snapshot": {}
    }
    try:
        import os
        os.makedirs("data_store", exist_ok=True)
        with open(os.path.join("data_store", "decisions.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception as e:
        print(f"[AUDIT ERROR] Failed to write kill switch log: {e}")


async def _flatten_all_positions(session: Session, executor: TwakExecutor):
    """Closes every open position (spot AND perp) at market. Used by both the
    recoverable de-risk tier and the hard halt."""
    positions = get_positions(session)
    if not positions:
        return
    from data.cmc_client import fetch_cmc_quotes
    quotes = await fetch_cmc_quotes()

    for pos in positions:
        kq = quotes.get(pos.symbol.upper())
        kill_price = kq.price if kq else 0.0
        print(f"[KILL SWITCH] Closing {pos.symbol} {getattr(pos,'direction','long')} (${pos.invested:.2f})")
        try:
            if getattr(pos, "is_perp", False):
                res = await executor.close_perp(
                    symbol=pos.symbol, direction=getattr(pos, "direction", "long"),
                    size_units=pos.size, entry_price=pos.entry_price,
                    margin_usd=pos.invested, leverage=getattr(pos, "leverage", 1.0),
                    ref_price=kill_price,
                )
            else:
                res = await executor.swap(
                    token_in=pos.contract, token_out=executor.settings.usdt_contract,
                    amount_in=Decimal(str(pos.size)), min_out=Decimal("0.0"),
                    reason="KILL_SWITCH", ref_price=kill_price,
                )
            closed_at = datetime.now(timezone.utc).isoformat()
            hold_min = (datetime.now(timezone.utc).timestamp() - pos.opened_at) / 60.0
            pnl_usd = (res.amount_out - pos.invested) if res and res.success else -pos.invested
            trade = session.get(Trade, pos.id)
            if trade:
                trade.status = "win" if pnl_usd > 0 else "loss"
                trade.closed_at = closed_at
                trade.pnl_usd = round(pnl_usd, 4)
                trade.pnl_pct = (pnl_usd / pos.invested) * 100.0 if pos.invested > 0 else -100.0
                trade.hold_minutes = hold_min
                trade.exit_reason = "KILL_SWITCH"
                trade.tx_close = res.tx_hash if res else ""
                trade.exit_price = (res.executed_price if res else 0.0) or kill_price
                session.add(trade)
                session.commit()
        except Exception as e:
            print(f"[KILL SWITCH ERROR] Failed to close {pos.symbol}: {e}")
        remove_position(session, pos.id)


async def check_kill_switch(session: Session, current_equity: float, executor: TwakExecutor) -> bool:
    """Two-tier drawdown protection sized for the ~30% disqualification gate.

    HARD HALT (returns True, stops the engine):
        - equity < $1.10 (cannot trade), or
        - drawdown >= dq_drawdown_pct - 3 (e.g. 27%) — better to lock the loss
          than risk being DISQUALIFIED at 30%.
    SOFT DE-RISK (recoverable, returns False so the engine stays alive):
        - drawdown >= flatten_drawdown_pct (e.g. 22%) — flatten all positions to
          stop the bleed and pause NEW discretionary entries for risk_pause_min,
          but keep the daily qualifier + monitoring running so we don't forfeit
          the min-1-trade/day rule for the rest of the week.
    """
    state = get_state(session)
    peak = state.peak_equity
    dd_pct = ((peak - current_equity) / peak) * 100.0 if peak > 0 else 0.0

    flatten_lvl = float(settings.flatten_drawdown_pct)
    dq_hard = max(flatten_lvl + 2.0, float(settings.dq_drawdown_pct) - 3.0)

    # --- HARD HALT ---
    if current_equity < 1.10 or dd_pct >= dq_hard:
        reason = (f"Portfolio ${current_equity:.2f} < $1.10" if current_equity < 1.10
                  else f"Drawdown {dd_pct:.1f}% >= hard halt {dq_hard:.0f}% (DQ cap {settings.dq_drawdown_pct:.0f}%). "
                       f"Peak=${peak:.2f}, Current=${current_equity:.2f}")
        print(f"[KILL SWITCH] HARD HALT: {reason}")
        update_state(session, scheduler_state="HALTED")
        write_kill_audit_log(f"HARD HALT: {reason}. All positions flattened, engine halted.", "KILL_SWITCH")
        await _flatten_all_positions(session, executor)
        return True

    # --- SOFT DE-RISK (recoverable) ---
    if dd_pct >= flatten_lvl:
        now = time.time()
        if now >= (state.risk_paused_until or 0.0):
            reason = (f"Drawdown {dd_pct:.1f}% >= flatten {flatten_lvl:.0f}%. Flattening + pausing new "
                      f"entries {settings.risk_pause_min:.0f}m (qualifier + monitor stay live). "
                      f"Peak=${peak:.2f}, Current=${current_equity:.2f}")
            print(f"[KILL SWITCH] SOFT DE-RISK: {reason}")
            write_kill_audit_log(f"SOFT DE-RISK: {reason}", "DE_RISK")
            await _flatten_all_positions(session, executor)
            update_state(session, risk_paused_until=now + float(settings.risk_pause_min) * 60.0)
        return False

    return False
