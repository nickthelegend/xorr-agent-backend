import time
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from sqlmodel import Session
from config import settings
from persistence.models import Position, Trade
from persistence.repo import get_positions, remove_position, add_trade
from core.twak_executor import TwakExecutor
from data.cmc_client import fetch_cmc_quotes
from filters.cooldown import apply_cooldown
from api.stream import log_broadcaster, log_engine_msg

async def monitor_tick(session: Session, executor: TwakExecutor):
    """
    Called every 60s (or monitor cadence) to poll active positions,
    evaluate exits (SL, TP, trailing profit lock, time-stops),
    and execute closures via TWAK.
    """
    positions = get_positions(session)
    if not positions:
        return

    # Fetch fresh quotes for all tokens
    quotes = await fetch_cmc_quotes()
    now_ts = time.time()
    now_dt = datetime.now(timezone.utc)
    
    for pos in positions:
        symbol = pos.symbol
        quote = quotes.get(symbol.upper())
        
        if not quote:
            print(f"[MONITOR WARNING] No price quote found for active position {symbol}. Skipping checks.")
            continue
            
        current_price = quote.price
        entry_price = pos.entry_price
        pnl_pct = ((current_price - entry_price) / entry_price) * 100.0
        pnl_usd = (pnl_pct / 100.0) * pos.invested
        
        hold_min = (now_ts - pos.opened_at) / 60.0
        
        # Check exit conditions
        exit_triggered = False
        exit_reason = None
        
        # 1. Hard Time-stop
        # news_catalyst = 25m, capitulation = 60m, momentum = 180m
        max_hold = 180
        if "news" in pos.strategy.lower():
            max_hold = 25
        elif "capitulation" in pos.strategy.lower():
            max_hold = 60
            
        if hold_min >= max_hold:
            exit_triggered = True
            exit_reason = "MAX_HOLD_TIME"
            
        # 2. Stop Loss (SL)
        elif current_price <= pos.stop_loss and not pos.tp1_hit:
            exit_triggered = True
            exit_reason = "SL_HIT"
            
        # 3. Take Profit (TP)
        elif pos.take_profit > 0 and current_price >= pos.take_profit and not pos.tp1_hit:
            exit_triggered = True
            exit_reason = "TP_HIT"
            
        # 4. Trailing stop check (for momentum_pullback or general trailing profit lock)
        elif "momentum" in pos.strategy.lower():
            # If +2% reached, lock profit and activate trailing stop of 1.5% from peak
            if pnl_pct >= 2.0 and not pos.tp1_hit:
                pos.tp1_hit = True
                # Set initial trailing stop at current_price - 1.5%
                pos.stop_loss = current_price * 0.985
                session.add(pos)
                session.commit()
                await log_engine_msg(session, "info", f"[MONITOR] Profit lock triggered for {symbol}: +2.0% reached. Trailing SL activated at ${pos.stop_loss:.4f}")
                
            elif pos.tp1_hit:
                # If price moves higher, raise trailing stop
                peak_trailing_stop = current_price * 0.985
                if peak_trailing_stop > pos.stop_loss:
                    pos.stop_loss = peak_trailing_stop
                    session.add(pos)
                    session.commit()
                    
                # Exit if price drops below trailing stop
                if current_price <= pos.stop_loss:
                    exit_triggered = True
                    exit_reason = "TRAIL_STOP_PROFIT"
                    
        # Check stagnation (e.g. flat trade after 45 minutes of no movement)
        if not exit_triggered and hold_min > 45.0 and abs(pnl_pct) < 0.2:
            exit_triggered = True
            exit_reason = "STAGNATION_EXIT"

        if exit_triggered:
            await log_engine_msg(session, "warn", f"[MONITOR EXIT] Closing {symbol} position. Reason={exit_reason}, Hold={hold_min:.1f}m, PnL={pnl_pct:.2f}% (${pnl_usd:.2f})")
            
            # Execute exit swap: sell token units back to USDT
            try:
                res = await executor.swap(
                    token_in=pos.contract,
                    token_out=settings.usdt_contract,
                    amount_in=Decimal(str(pos.size)),
                    min_out=Decimal("0.0"),  # slippage verified in entry, exit runs at market
                    reason=f"EXIT_{exit_reason}",
                    ref_price=current_price
                )
                
                if res.success:
                    pnl_realized = res.amount_out - pos.invested
                    pct_realized = (pnl_realized / pos.invested) * 100.0 if pos.invested > 0 else 0.0
                    trade_status = "win" if pnl_realized > 0 else "loss"
                    
                    # Update Trade entry
                    trade = session.get(Trade, pos.id)
                    if trade:
                        trade.status = trade_status
                        trade.closed_at = now_dt.isoformat()
                        trade.pnl_usd = round(pnl_realized, 4)
                        trade.pnl_pct = round(pct_realized, 2)
                        trade.hold_minutes = round(hold_min, 1)
                        trade.exit_reason = exit_reason
                        trade.tx_close = res.tx_hash
                        trade.exit_market_cap = quote.market_cap
                        session.add(trade)
                        session.commit()
                        
                    await log_engine_msg(session, "info", f"[MONITOR SUCCESS] Exited {symbol}. realized PnL=${pnl_realized:.2f} ({pct_realized:.1f}%)")
                    
                    # Apply Cooldown to this token
                    apply_cooldown(session, symbol, trade_status, hold_min)
                else:
                    await log_engine_msg(session, "error", f"[MONITOR ERROR] Exit swap failed for {symbol}: {res.error}")
            except Exception as e:
                await log_engine_msg(session, "error", f"[MONITOR ERROR] Exception during exit swap for {symbol}: {e}")
                
            # Remove from active positions database
            remove_position(session, pos.id)
