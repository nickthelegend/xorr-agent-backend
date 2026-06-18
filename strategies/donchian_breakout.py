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


class DonchianBreakoutStrategy(BaseStrategy):
    """Trend-following 1h Donchian channel breakout (the price core of senpi's
    badger/hawk). Targets bigger moves with a wide stop so the ~0.7% round-trip
    cost is a small fraction of the expected win — the opposite of the cost-killed
    +2% scalp setups. Live mode can additionally gate on rising open interest via
    the CMC `detect_oi_dark_flow_setup` skill."""

    def __init__(self):
        super().__init__("donchian_breakout")

    async def evaluate(self, symbol, candles_5m, candles_1h, market_ctx: MarketContext) -> Optional[Signal]:
        # Breakout is trend-following -> require momentum confluence
        from filters.confluence_score import gate_threshold
        if market_ctx.confluence < gate_threshold():
            return None

        if len(candles_1h) < 25 or len(candles_5m) < 12:
            return None

        highs_1h = [c.high for c in candles_1h]
        closes_1h = [c.close for c in candles_1h]
        vols_1h = [c.volume for c in candles_1h]

        current_close = closes_1h[-1]

        # 1. Uptrend filter: price above 1h 50-EMA
        ema_50 = _ema(closes_1h, 50) if len(closes_1h) >= 50 else _ema(closes_1h, 20)
        if current_close <= ema_50:
            return None

        # 2. Donchian breakout: current close exceeds the highest high of the prior
        #    20 completed 1h bars (the channel), and the prior bar did NOT (fresh break)
        channel_high = max(highs_1h[-21:-1])
        prev_close = closes_1h[-2]
        if not (current_close > channel_high and prev_close <= channel_high):
            return None

        # 3. Volume confirmation: breakout bar volume > 1.3x the 20-bar average
        avg_vol = sum(vols_1h[-21:-1]) / 20.0
        if avg_vol <= 0 or vols_1h[-1] < 1.3 * avg_vol:
            return None

        # 4. Don't chase a bar that has already extended > 6% above the channel
        if (current_close - channel_high) / channel_high > 0.06:
            return None

        token = resolve(symbol)
        contract = token.contract if token else ""

        # Wide stop just under the breakout level; large target to let the move run
        # (a trailing stop is applied by the monitor/backtest for this strategy).
        stop_loss_pct = max(2.0, min(4.0, ((current_close - channel_high) / current_close) * 100.0 + 2.0))

        return Signal(
            symbol=symbol,
            contract=contract,
            side="buy",
            confidence=0.8,
            stop_loss_pct=round(stop_loss_pct, 2),
            take_profit_pct=6.0,      # let winners run; trailing locks profit en route
            max_hold_min=600,
            rationale=f"Donchian breakout: 1h close ${current_close:.4f} cleared 20-bar channel high ${channel_high:.4f} on {vols_1h[-1]/avg_vol:.1f}x volume, above 50-EMA.",
            strategy_name=self.name,
        )
