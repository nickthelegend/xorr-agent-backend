import time
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from sqlmodel import Session
from config import settings
from persistence.models import Position, Trade
from persistence.repo import get_positions, remove_position, add_trade
from core.twak_executor import TwakExecutor
from core import perp_math
from data.cmc_client import fetch_cmc_quotes, fetch_fast_quotes
from filters.cooldown import apply_cooldown
from api.stream import log_broadcaster, log_engine_msg


def _max_hold_min(strategy: str) -> int:
    s = (strategy or "").lower()
    if "news" in s:
        return 25
    if "capitulation" in s:
        return 60
    if "donchian_perp" in s or "perp" in s:
        return 720
    if "donchian" in s or "xsect" in s:
        return 600
    if "trend_follow" in s:
        return 360
    return 180


def _adverse_hit(direction: str, mark: float, stop: float) -> bool:
    """Has price moved against the position past its stop?"""
    if stop <= 0:
        return False
    if direction == "short":
        return mark >= stop
    return mark <= stop


def _tp_hit(direction: str, mark: float, tp: float) -> bool:
    if tp <= 0:
        return False
    if direction == "short":
        return mark <= tp
    return mark >= tp


async def monitor_tick(session: Session, executor: TwakExecutor):
    """
    Called every 60s to poll active positions (spot AND perp, long AND short),
    evaluate exits (time-stop, SL, TP, trailing profit-lock, liquidation guard,
    stagnation) direction-aware, and execute closures via TWAK.
    """
    positions = get_positions(session)
    if not positions:
        return

    # Fast, low-latency marks (Binance ~8s cache) so exits/liquidation react
    # quickly; fall back to the CMC quote set if Binance is unreachable.
    quotes = await fetch_fast_quotes()
    if not quotes:
        quotes = await fetch_cmc_quotes()
    now_ts = time.time()
    now_dt = datetime.now(timezone.utc)
    liq_guard_pct = float(getattr(settings, "perp_liq_guard_pct", 6.0))

    for pos in positions:
        symbol = pos.symbol
        quote = quotes.get(symbol.upper())
        if not quote:
            print(f"[MONITOR WARNING] No price quote for {symbol}. Skipping checks.")
            continue

        is_perp = bool(getattr(pos, "is_perp", False))
        direction = getattr(pos, "direction", "long")
        leverage = float(getattr(pos, "leverage", 1.0) or 1.0)
        sign = -1.0 if direction == "short" else 1.0

        # Mark price: prefer the perp venue's own mark in live mode so we manage
        # the position against the price it would actually be liquidated at.
        mark = quote.price
        if is_perp and not executor.simulation:
            venue_mark = await executor.perp_mark(symbol)
            if venue_mark:
                mark = venue_mark

        entry_price = pos.entry_price
        # Favorable move in PRICE terms (+ve = in profit), direction-aware.
        fav_pct = sign * ((mark - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0.0
        # PnL on capital at risk (margin for perps, notional for spot).
        cap_pnl_pct = fav_pct * (leverage if is_perp else 1.0)
        pnl_usd_est = (cap_pnl_pct / 100.0) * pos.invested

        hold_min = (now_ts - pos.opened_at) / 60.0

        exit_triggered = False
        exit_reason = None

        # 1. Hard time-stop
        if hold_min >= _max_hold_min(pos.strategy):
            exit_triggered = True
            exit_reason = "MAX_HOLD_TIME"

        # 2. Liquidation guard (perp only) — close well before the liquidation
        #    price. With 3x + ~3% stops this should never trigger, but a gap
        #    must never be allowed to reach liquidation (protects the DQ gate).
        elif is_perp and perp_math.liquidation_distance_pct(pos, mark) <= liq_guard_pct:
            exit_triggered = True
            exit_reason = "LIQ_GUARD"

        # 3. Stop loss (adverse move past the stop), before any profit-lock
        elif not pos.tp1_hit and _adverse_hit(direction, mark, pos.stop_loss):
            exit_triggered = True
            exit_reason = "SL_HIT"

        # 4. Take profit
        elif not pos.tp1_hit and _tp_hit(direction, mark, pos.take_profit):
            exit_triggered = True
            exit_reason = "TP_HIT"

        # 5. Profit-lock + trailing stop (direction-aware)
        else:
            strat = (pos.strategy or "").lower()
            # momentum (spot long): lock at +2%, trail 1.5%
            if "momentum" in strat and not is_perp:
                if fav_pct >= 2.0 and not pos.tp1_hit:
                    pos.tp1_hit = True
                    pos.stop_loss = mark * 0.985
                    session.add(pos); session.commit()
                    await log_engine_msg(session, "info", f"[MONITOR] Profit-lock {symbol}: +2.0%, trailing SL ${pos.stop_loss:.4f}")
                elif pos.tp1_hit:
                    new_stop = mark * 0.985
                    if new_stop > pos.stop_loss:
                        pos.stop_loss = new_stop; session.add(pos); session.commit()
                    if mark <= pos.stop_loss:
                        exit_triggered = True; exit_reason = "TRAIL_STOP_PROFIT"
            # breakout family (spot or perp, long or short): lock at +2.5%, trail 3%
            elif "donchian" in strat or "xsect" in strat or "perp" in strat:
                if fav_pct >= 2.5 and not pos.tp1_hit:
                    pos.tp1_hit = True
                    if direction == "short":
                        # stop ABOVE: lock at min(existing, entry-, mark+3%)
                        lock = min(pos.stop_loss if pos.stop_loss > 0 else 1e18,
                                   entry_price * 0.995, mark * 1.03)
                        pos.stop_loss = lock
                    else:
                        pos.stop_loss = max(pos.stop_loss, entry_price * 1.005, mark * 0.97)
                    session.add(pos); session.commit()
                    await log_engine_msg(session, "info", f"[MONITOR] Breakout profit-lock {symbol} ({direction}): +2.5%, trailing SL ${pos.stop_loss:.4f}")
                elif pos.tp1_hit:
                    if direction == "short":
                        new_stop = mark * 1.03
                        if new_stop < pos.stop_loss:
                            pos.stop_loss = new_stop; session.add(pos); session.commit()
                        if mark >= pos.stop_loss:
                            exit_triggered = True; exit_reason = "TRAIL_STOP_PROFIT"
                    else:
                        new_stop = mark * 0.97
                        if new_stop > pos.stop_loss:
                            pos.stop_loss = new_stop; session.add(pos); session.commit()
                        if mark <= pos.stop_loss:
                            exit_triggered = True; exit_reason = "TRAIL_STOP_PROFIT"

        # 6. Stagnation: flat trade with no edge after 45m
        if not exit_triggered and hold_min > 45.0 and abs(fav_pct) < 0.2 and not pos.tp1_hit:
            exit_triggered = True
            exit_reason = "STAGNATION_EXIT"

        if not exit_triggered:
            continue

        await log_engine_msg(
            session, "warn",
            f"[MONITOR EXIT] Closing {symbol} {direction}{'·perp' if is_perp else ''}. "
            f"Reason={exit_reason}, Hold={hold_min:.1f}m, move={fav_pct:.2f}% pnl=${pnl_usd_est:.2f}"
        )

        try:
            if is_perp:
                res = await executor.close_perp(
                    symbol=symbol, direction=direction, size_units=pos.size,
                    entry_price=entry_price, margin_usd=pos.invested,
                    leverage=leverage, ref_price=mark,
                )
            else:
                res = await executor.swap(
                    token_in=pos.contract, token_out=settings.usdt_contract,
                    amount_in=Decimal(str(pos.size)), min_out=Decimal("0.0"),
                    reason=f"EXIT_{exit_reason}", ref_price=mark,
                )

            if res.success:
                pnl_realized = res.amount_out - pos.invested
                pct_realized = (pnl_realized / pos.invested) * 100.0 if pos.invested > 0 else 0.0
                trade_status = "win" if pnl_realized > 0 else "loss"

                trade = session.get(Trade, pos.id)
                if trade:
                    trade.status = trade_status
                    trade.closed_at = now_dt.isoformat()
                    trade.pnl_usd = round(pnl_realized, 4)
                    trade.pnl_pct = round(pct_realized, 2)
                    trade.hold_minutes = round(hold_min, 1)
                    trade.exit_reason = exit_reason
                    trade.tx_close = res.tx_hash
                    trade.exit_price = res.executed_price or mark
                    trade.exit_mc = quote.market_cap
                    session.add(trade)
                    session.commit()

                await log_engine_msg(session, "info", f"[MONITOR SUCCESS] Exited {symbol}. realized PnL=${pnl_realized:.2f} ({pct_realized:.1f}% on capital)")
                apply_cooldown(session, symbol, trade_status, hold_min)
            else:
                await log_engine_msg(session, "error", f"[MONITOR ERROR] Exit failed for {symbol}: {res.error}")
        except Exception as e:
            await log_engine_msg(session, "error", f"[MONITOR ERROR] Exception during exit for {symbol}: {e}")

        remove_position(session, pos.id)
