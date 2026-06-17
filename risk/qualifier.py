import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from sqlmodel import Session, select
from persistence.models import Trade
from persistence.repo import add_trade
from core.twak_executor import TwakExecutor
from data.tokens import resolve

BTCB_CONTRACT = "0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c"

def get_ist_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))

def is_qualifier_trade_needed(session: Session) -> bool:
    """
    Returns True if:
    1. Current time is >= 11:30 AM IST (Hyderabad timezone).
    2. No trades have been executed since the start of the current IST day.
    """
    ist_now = get_ist_now()
    
    # Check if it is at or after 11:30 AM IST
    if ist_now.hour < 11 or (ist_now.hour == 11 and ist_now.minute < 30):
        return False
        
    # Start of today in IST (UTC equivalent)
    ist_start_of_day = ist_now.replace(hour=0, minute=0, second=0, microsecond=0)
    utc_start_of_day = ist_start_of_day.astimezone(timezone.utc)
    
    # Query trades opened since start of today
    statement = select(Trade).where(Trade.opened_at >= utc_start_of_day.isoformat())
    today_trades = session.exec(statement).all()
    
    return len(today_trades) == 0

async def execute_qualifier_trade(session: Session, executor: TwakExecutor):
    """Executes a $1.50 USDT -> BTCB -> USDT qualifier round-trip trade."""
    print("[QUALIFIER] Starting daily minimum trade routine ($1.50 USDT -> BTCB -> USDT).")
    
    usdt_contract = executor.settings.usdt_contract
    btcb_info = resolve("BTCB")
    btcb_contract = btcb_info.contract if btcb_info else BTCB_CONTRACT
    
    opened_at = datetime.now(timezone.utc).isoformat()
    trade_id = f"QUAL:{uuid.uuid4()}"
    
    # Phase 1: Swap USDT -> BTCB
    buy_amount = Decimal("1.50")
    print(f"[QUALIFIER] Swapping $1.50 USDT -> BTCB...")
    
    # We estimate received tokens based on reference price or quote
    # Default BTCB price around $60,000, so $1.50 = 0.000025 BTCB
    min_btcb_out = Decimal("0.00002")  # soft min
    
    buy_res = await executor.swap(
        token_in=usdt_contract,
        token_out=btcb_contract,
        amount_in=buy_amount,
        min_out=min_btcb_out,
        reason="QUALIFIER_ENTRY"
    )
    
    if not buy_res.success:
        print(f"[QUALIFIER ERROR] Entry swap failed: {buy_res.error}")
        return
        
    # Record open trade
    open_trade = Trade(
        id=trade_id,
        opened_at=opened_at,
        closed_at=None,
        symbol="BTCB",
        contract=btcb_contract,
        status="open",
        invested=1.50,
        pnl_usd=0.0,
        pnl_pct=0.0,
        hold_minutes=0.0,
        entry_mc=0.0,
        exit_mc=0.0,
        score=99.0,
        exit_reason=None,
        window="QUALIFIER",
        tx_open=buy_res.tx_hash,
        tx_close=None,
        strategy="qualifier_round_trip"
    )
    add_trade(session, open_trade)
    
    # Hold for 60 seconds to satisfy minimum mempool confirmation and hold conditions
    print("[QUALIFIER] Swap entry complete. Holding for 60 seconds...")
    await asyncio.sleep(60)
    
    # Phase 2: Swap BTCB -> USDT
    sell_qty = Decimal(str(buy_res.amount_out))
    print(f"[QUALIFIER] Swapping {sell_qty} BTCB back to USDT...")
    
    sell_res = await executor.swap(
        token_in=btcb_contract,
        token_out=usdt_contract,
        amount_in=sell_qty,
        min_out=Decimal("0.0"),
        reason="QUALIFIER_EXIT"
    )
    
    closed_at = datetime.now(timezone.utc).isoformat()
    hold_min = (datetime.now(timezone.utc).timestamp() - datetime.fromisoformat(opened_at).timestamp()) / 60.0
    
    # Update trade in DB
    trade = session.get(Trade, trade_id)
    if trade:
        pnl = sell_res.amount_out - 1.50 if sell_res.success else -1.50
        status = "win" if pnl > 0 else "loss"
        
        trade.status = status
        trade.closed_at = closed_at
        trade.pnl_usd = round(pnl, 4)
        trade.pnl_pct = round((pnl / 1.50) * 100.0, 2)
        trade.hold_minutes = round(hold_min, 2)
        trade.exit_reason = "MAX_HOLD_TIME" if sell_res.success else "STAGNATION_EXIT"
        trade.tx_close = sell_res.tx_hash
        session.add(trade)
        session.commit()
        print(f"[QUALIFIER] Routine completed. Status={status}, PnL=${pnl:.4f}, Tx={sell_res.tx_hash}")
    else:
        print("[QUALIFIER ERROR] Trade record not found during close update.")
