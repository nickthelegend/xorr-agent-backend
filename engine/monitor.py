import time
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from sqlmodel import Session
from config import settings
from persistence.models import Position, Trade, StrategyStat
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


def _r_multiple(pos, exit_price: float) -> float:
    """Realized R = price move / initial-stop distance (direction-aware, leverage
    cancels). Uses the NEVER-trailed init_stop so the R reflects the real risk taken."""
    entry = pos.entry_price
    init_stop = getattr(pos, "init_stop", 0.0) or pos.stop_loss
    if entry <= 0 or init_stop <= 0 or exit_price <= 0:
        return 0.0
    stop_frac = abs(entry - init_stop) / entry
    if stop_frac <= 0:
        return 0.0
    sign = -1.0 if getattr(pos, "direction", "long") == "short" else 1.0
    move_frac = (exit_price - entry) / entry * sign
    return move_frac / stop_frac


def _price_for_r(pos, r: float) -> float:
    """The price that locks in `r` R of profit (direction-aware), using the ORIGINAL stop
    distance so trailing R stays anchored to the real risk that was taken."""
    entry = pos.entry_price
    init_stop = getattr(pos, "init_stop", 0.0) or pos.stop_loss
    if entry <= 0 or init_stop <= 0:
        return 0.0
    stop_frac = abs(entry - init_stop) / entry
    sign = -1.0 if getattr(pos, "direction", "long") == "short" else 1.0
    return entry * (1.0 + sign * r * stop_frac)


def _record_close(session, pos, now_dt, exit_price, pnl_usd, pnl_pct, hold_min, reason, r_mult, tx, window=None):
    """Update/create the Trade row and ALWAYS write a StrategyStat (the rolling-R
    series the arbiter uses to suspend/revive/promote — real AND shadow)."""
    status = "win" if pnl_usd > 0 else "loss"
    trade = session.get(Trade, pos.id)
    if trade is None:
        trade = Trade(id=pos.id, opened_at=datetime.fromtimestamp(pos.opened_at, timezone.utc).isoformat(),
                      symbol=pos.symbol, contract=pos.contract, status=status,
                      invested=pos.invested, window=window or "COMPETITION", tx_open=pos.id,
                      strategy=pos.strategy, direction=getattr(pos, "direction", "long"),
                      venue=getattr(pos, "venue", "spot"), leverage=getattr(pos, "leverage", 1.0))
    trade.status = status
    trade.closed_at = now_dt.isoformat()
    trade.pnl_usd = round(pnl_usd, 4)
    trade.pnl_pct = round(pnl_pct, 2)
    trade.hold_minutes = round(hold_min, 1)
    trade.exit_reason = reason
    trade.tx_close = tx
    trade.exit_price = exit_price
    if window:
        trade.window = window
    session.add(trade)
    session.add(StrategyStat(strategy=pos.strategy, trade_id=pos.id, closed_at=now_dt,
                             r_realized=round(r_mult, 3), pnl_usd=round(pnl_usd, 4)))
    session.commit()


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

        # 5. Universal risk-free profit engine (EVERY position incl. Claude reversion picks):
        #    once a trade is +1R in our favour, jump the stop to BREAKEVEN so it can no longer
        #    lose; past +trail_trigger_r, ratchet a trailing stop that locks in profit and lets
        #    the winner run. This is the "no-risk-after-it-works" mechanism.
        else:
            r_now = _r_multiple(pos, mark)            # unrealised R vs the ORIGINAL stop
            be_r = float(getattr(settings, "breakeven_trigger_r", 1.0))
            trail_r = float(getattr(settings, "trail_trigger_r", 1.6))
            giveback = float(getattr(settings, "trail_giveback_r", 0.8))
            fee_buf = float(getattr(settings, "breakeven_fee_buffer", 0.004))

            # A. risk-free: at +be_r, move the stop to breakeven (+fees), once.
            if not pos.tp1_hit and r_now >= be_r:
                pos.tp1_hit = True
                if direction == "short":
                    be = entry_price * (1.0 - fee_buf)
                    pos.stop_loss = be if pos.stop_loss <= 0 else min(pos.stop_loss, be)
                else:
                    be = entry_price * (1.0 + fee_buf)
                    pos.stop_loss = max(pos.stop_loss, be)
                session.add(pos); session.commit()
                await log_engine_msg(session, "info",
                    f"[MONITOR] RISK-FREE {symbol} ({direction}): +{r_now:.2f}R → stop to breakeven ${pos.stop_loss:.6g}")

            # B. trail: past +trail_r, ratchet the stop to lock (r_now - giveback)R.
            if pos.tp1_hit and r_now >= trail_r:
                new_stop = _price_for_r(pos, max(0.0, r_now - giveback))
                if new_stop > 0:
                    if direction == "short":
                        if pos.stop_loss <= 0 or new_stop < pos.stop_loss:
                            pos.stop_loss = new_stop; session.add(pos); session.commit()
                    else:
                        if new_stop > pos.stop_loss:
                            pos.stop_loss = new_stop; session.add(pos); session.commit()

            # C. once armed (breakeven or trailing), exit if that stop is hit.
            if pos.tp1_hit and _adverse_hit(direction, mark, pos.stop_loss):
                exit_triggered = True
                exit_reason = "TRAIL_STOP_PROFIT" if r_now > be_r + 0.05 else "BREAKEVEN_STOP"

        # 6. Stagnation: flat trade with no edge after 45m
        if not exit_triggered and hold_min > 45.0 and abs(fav_pct) < 0.2 and not pos.tp1_hit:
            exit_triggered = True
            exit_reason = "STAGNATION_EXIT"

        if not exit_triggered:
            continue

        r_mult = _r_multiple(pos, mark)

        # --- SHADOW positions: paper close, NO execution / NO capital, but record
        #     the Trade + StrategyStat so the arbiter can promote a proven strategy.
        if getattr(pos, "is_shadow", False):
            cap_pnl_pct = fav_pct * (leverage if is_perp else 1.0)
            pnl_usd = pos.invested * cap_pnl_pct / 100.0
            _record_close(session, pos, now_dt, mark, pnl_usd, cap_pnl_pct, hold_min,
                          exit_reason, r_mult, tx="SHADOW", window="SHADOW")
            await log_engine_msg(session, "info", f"[shadow] {pos.strategy} {symbol} paper-closed {exit_reason}: {r_mult:+.2f}R")
            remove_position(session, pos.id)
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
                    leverage=leverage, ref_price=mark, hold_hours=hold_min / 60.0,
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
                exit_px = res.executed_price or mark
                _record_close(session, pos, now_dt, exit_px, pnl_realized, pct_realized,
                              hold_min, exit_reason, r_mult, tx=res.tx_hash)
                await log_engine_msg(session, "info", f"[MONITOR SUCCESS] Exited {symbol}. realized PnL=${pnl_realized:.2f} ({pct_realized:.1f}% on capital, {r_mult:+.2f}R)")
                apply_cooldown(session, symbol, trade_status, hold_min)
            else:
                await log_engine_msg(session, "error", f"[MONITOR ERROR] Exit failed for {symbol}: {res.error}")
        except Exception as e:
            await log_engine_msg(session, "error", f"[MONITOR ERROR] Exception during exit for {symbol}: {e}")

        remove_position(session, pos.id)
