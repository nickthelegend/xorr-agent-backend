from typing import Optional, List
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from data.tokens import resolve
from config import settings


def _ema(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0.0
    k = 2.0 / (period + 1.0)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = (p - ema) * k + ema
    return ema


class DonchianPerpStrategy(BaseStrategy):
    """Leveraged LONG/SHORT breakout on liquid perp majors — the hawk archetype.

    LONG  on a fresh 1h close ABOVE the N-bar channel high while price is above
          the 50-EMA, on a volume expansion.
    SHORT on a fresh 1h close BELOW the N-bar channel low while price is below
          the 50-EMA, on a volume expansion.

    Direction is regime-gated INSIDE the strategy (is_actionable lets the perp
    book run in every regime): longs in TREND_UP/CHOP, shorts in
    TREND_DOWN/RISK_OFF/CHOP. Executed as a leveraged perp (venue="perp") via
    TWAK/Aster, so the agent profits when the market FALLS — the structural edge
    the long-only spot book lacks, and the difference between "breakeven in a
    down week" and "wins the week".

    Stops are kept in PRICE terms but sized for leverage: a ~3% price stop at 3x
    is ~9% on margin, while the ~33%-away liquidation price is never approached.
    """

    def __init__(self):
        super().__init__("donchian_perp")

    @staticmethod
    def _perp_symbols() -> set:
        raw = getattr(settings, "perp_symbols", "") or ""
        return {s.strip().upper() for s in raw.split(",") if s.strip()}

    def _mk(self, symbol: str, direction: str, price: float, level: float,
            leverage: float, vol_mult: float) -> Signal:
        # Stop just beyond the broken channel level, clamped to a leverage-aware band.
        raw_stop = abs(price - level) / price * 100.0 + 1.0
        stop_loss_pct = round(max(2.0, min(3.5, raw_stop)), 2)
        token = resolve(symbol)
        contract = token.contract if token else ""
        side = "buy" if direction == "long" else "sell"
        arrow = "above" if direction == "long" else "below"
        return Signal(
            symbol=symbol,
            contract=contract,
            side=side,
            confidence=0.78,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=7.0,        # let winners run; monitor trails the leveraged move
            max_hold_min=720,
            rationale=(f"{direction.upper()} perp {leverage:.0f}x: 1h close ${price:.4f} broke "
                       f"{arrow} the 20-bar channel ${level:.4f} on {vol_mult:.1f}x volume, "
                       f"{'above' if direction=='long' else 'below'} 50-EMA."),
            strategy_name=self.name,
            direction=direction,
            venue="perp",
            leverage=leverage,
        )

    async def evaluate(self, symbol, candles_5m, candles_1h, market_ctx: MarketContext) -> Optional[Signal]:
        if symbol.upper() not in self._perp_symbols():
            return None
        if len(candles_1h) < 25:
            return None

        leverage = float(getattr(settings, "perp_leverage", 3.0))
        regime = market_ctx.regime
        # Regime DISCIPLINE: only trade breakouts WITH a confirmed trend. Breakouts
        # in CHOP are fakeouts that whipsaw the book (backtest-validated: allowing
        # CHOP cut expectancy hard and doubled drawdown). Same lesson the spot
        # momentum_pullback learned. Long only in TREND_UP; short only in a
        # confirmed downtrend / risk-off.
        allow_long = regime == "TREND_UP"
        allow_short = regime in ("TREND_DOWN", "RISK_OFF")
        if not (allow_long or allow_short):
            return None

        highs = [c.high for c in candles_1h]
        lows = [c.low for c in candles_1h]
        closes = [c.close for c in candles_1h]
        vols = [c.volume for c in candles_1h]

        cur = closes[-1]
        prev = closes[-2]
        ema_50 = _ema(closes, 50) if len(closes) >= 50 else _ema(closes, 20)

        avg_vol = sum(vols[-21:-1]) / 20.0
        vol_ok = avg_vol > 0 and vols[-1] >= 1.3 * avg_vol
        if not vol_ok:
            return None

        channel_high = max(highs[-21:-1])
        channel_low = min(lows[-21:-1])
        vol_mult = vols[-1] / avg_vol if avg_vol > 0 else 0.0

        # LONG breakout: fresh close above the channel, in an uptrend, not over-extended
        if allow_long and cur > ema_50:
            if cur > channel_high and prev <= channel_high and (cur - channel_high) / channel_high <= 0.06:
                return self._mk(symbol, "long", cur, channel_high, leverage, vol_mult)

        # SHORT breakdown: fresh close below the channel, in a downtrend, not over-extended
        if allow_short and cur < ema_50:
            if cur < channel_low and prev >= channel_low and (channel_low - cur) / channel_low <= 0.06:
                return self._mk(symbol, "short", cur, channel_low, leverage, vol_mult)

        return None
