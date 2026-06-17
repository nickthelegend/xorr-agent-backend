import uuid
import json
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from sqlmodel import Session
from persistence.repo import get_state, update_state, remove_position, add_trade, get_positions
from persistence.models import Position, Trade
from core.twak_executor import TwakExecutor

def write_kill_audit_log(reason: str):
    """Appends kill switch trip to decisions.jsonl audit log."""
    log_entry = {
        "id": str(uuid.uuid4()),
        "t": datetime.now(timezone.utc).isoformat(),
        "symbol": "SYSTEM",
        "action": "KILL_SWITCH",
        "strategy": "SYSTEM",
        "filters_passed": [],
        "filters_blocked": [],
        "brain_score": 0.0,
        "reasoning": f"EMERGENCY KILL SWITCH TRIPPED: {reason}. All active positions liquidated and engine halted.",
        "market_snapshot": {}
    }
    try:
        os_dir = "data_store"
        import os
        os.makedirs(os_dir, exist_ok=True)
        with open(os.path.join(os_dir, "decisions.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception as e:
        print(f"[AUDIT ERROR] Failed to write kill switch log: {e}")

async def check_kill_switch(session: Session, current_equity: float, executor: TwakExecutor) -> bool:
    """
    Checks risk limits. If total portfolio value < $1.10 or drawdown > 25%:
    1. Sets scheduler_state to 'HALTED'
    2. Liquidates all active positions
    3. Appends an audit trail entry
    """
    state = get_state(session)
    peak = state.peak_equity
    
    # Track drawdown
    dd_pct = 0.0
    if peak > 0:
        dd_pct = ((peak - current_equity) / peak) * 100.0
        
    tripped = False
    reason = ""
    
    if current_equity < 1.10:
        tripped = True
        reason = f"Portfolio equity ${current_equity:.2f} fell below minimum limit ($1.10)"
    elif dd_pct > 25.0:
        tripped = True
        reason = f"Max Drawdown exceeded 25% (Peak=${peak:.2f}, Current=${current_equity:.2f}, Drawdown={dd_pct:.1f}%)"
        
    if tripped:
        print(f"[KILL SWITCH TRIP] EMERGENCY TRIP: {reason}")
        # 1. Update state to HALTED
        update_state(session, scheduler_state="HALTED")
        write_kill_audit_log(reason)
        
        # 2. Liquidate open positions
        positions = get_positions(session)
        
        for pos in positions:
            print(f"[KILL SWITCH] Liquidating position in {pos.symbol} (${pos.invested:.2f})")
            
            # Close via executor
            # Swap token -> USDT
            token_addr = pos.contract
            qty = Decimal(str(pos.size))
            
            try:
                # Perform swap: sell held token units back to USDT
                res = await executor.swap(
                    token_in=token_addr,
                    token_out=executor.settings.usdt_contract,
                    amount_in=qty,
                    min_out=Decimal("0.0"),  # slippage ignored for market-kill liquidation
                    reason="KILL_SWITCH"
                )
                
                # Record the trade closure in the database
                closed_at = datetime.now(timezone.utc).isoformat()
                hold_min = (datetime.now(timezone.utc).timestamp() - pos.opened_at) / 60.0
                
                # Fetch trade object matching position ID
                trade = session.get(Trade, pos.id)
                if trade:
                    trade.status = "loss"
                    trade.closed_at = closed_at
                    trade.pnl_usd = res.amount_out - pos.invested
                    trade.pnl_pct = (trade.pnl_usd / pos.invested) * 100.0 if pos.invested > 0 else -100.0
                    trade.hold_minutes = hold_min
                    trade.exit_reason = "KILL_SWITCH"
                    trade.tx_close = res.tx_hash
                    session.add(trade)
                else:
                    # Create new trade entry just in case
                    new_trade = Trade(
                        id=pos.id,
                        opened_at=datetime.fromtimestamp(pos.opened_at, timezone.utc).isoformat(),
                        closed_at=closed_at,
                        symbol=pos.symbol,
                        contract=pos.contract,
                        status="loss",
                        invested=pos.invested,
                        pnl_usd=res.amount_out - pos.invested,
                        pnl_pct=(res.amount_out - pos.invested) / pos.invested * 100.0 if pos.invested > 0 else -100.0,
                        hold_minutes=hold_min,
                        exit_reason="KILL_SWITCH",
                        window="COMPETITION",
                        tx_open=pos.id,
                        tx_close=res.tx_hash,
                        strategy=pos.strategy
                    )
                    session.add(new_trade)
                    
            except Exception as e:
                print(f"[KILL SWITCH ERROR] Failed to swap liquidate {pos.symbol}: {e}")
                
            # Remove position from database regardless of swap success to prevent infinite loops
            remove_position(session, pos.id)
            
        return True
        
    return False
