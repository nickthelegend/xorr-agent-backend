from typing import Optional, List
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from data.tokens import resolve


def _ema(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0.0
    k = 2.0 / (period + 1.0)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = (p - ema) * k + ema
    return ema


def _rsi(prices: List[float], period: int) -> float:
    """Wilder RSI, returns the latest value."""
    if len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100.0 - (100.0 / (1.0 + rs))


class RsiReversionStrategy(BaseStrategy):
    """Connors-style RSI(2) mean reversion — buy a sharp oversold dip *within an
    uptrend* (price above the 1h 50-EMA), not a falling knife. High win rate, many
    small wins; modest TP that clears costs. Counter-trend → no momentum-confluence
    gate (oversold setups are intrinsically low-momentum)."""

    def __init__(self):
        super().__init__("rsi_reversion")

    async def evaluate(self, symbol, candles_5m, candles_1h, market_ctx: MarketContext) -> Optional[Signal]:
        if len(candles_5m) < 16 or len(candles_1h) < 50:
            return None

        closes_5m = [c.close for c in candles_5m]
        closes_1h = [c.close for c in candles_1h]
        price = closes_5m[-1]

        # 1. Uptrend filter: price above the 1h 50-EMA AND the trend RISING (EMA50
        # now above EMA50 ~10 bars ago) — only buy dips in a strengthening uptrend.
        ema_50_1h = _ema(closes_1h, 50)
        if price <= ema_50_1h:
            return None
        if len(closes_1h) >= 60:
            ema_50_prev = _ema(closes_1h[:-10], 50)
            if ema_50_1h <= ema_50_prev:
                return None

        # 2. Deep short-term oversold on 5m RSI(2)
        rsi2 = _rsi(closes_5m, 2)
        if rsi2 > 10.0:
            return None

        # 3. Bounce confirmation: the dip is turning up (last 5m candle green), not
        # a knife still falling.
        if closes_5m[-1] <= closes_5m[-2]:
            return None

        # 4. Not in freefall: dip < ~5% off the recent 12-bar high (pullback, not collapse)
        recent_high = max(c.high for c in candles_5m[-12:])
        if recent_high > 0 and (recent_high - price) / recent_high > 0.05:
            return None

        token = resolve(symbol)
        contract = token.contract if token else ""

        return Signal(
            symbol=symbol,
            contract=contract,
            side="buy",
            confidence=0.78,
            stop_loss_pct=1.6,
            take_profit_pct=1.6,   # mean-revert back toward the mean; small, clears cost
            max_hold_min=75,
            rationale=f"RSI(2) oversold bounce in uptrend: RSI2={rsi2:.1f}<10, price ${price:.4f} above 1h 50-EMA ${ema_50_1h:.4f}.",
            strategy_name=self.name,
        )
