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


class CrossSectionalMomentumStrategy(BaseStrategy):
    """Cross-sectional (relative-strength) momentum — the academically-validated
    'buy the strongest names, rotate' factor (Cambridge JFQA 2025; arXiv AdaptiveTrend
    2026). For each symbol, rank it by 24h return across the whole tradable universe
    in this scan; only the top decile, in a non-downtrend, above its 1h 20-EMA, and
    not already overextended, qualifies. Lets winners run with a wide trailing stop;
    cooldown prevents constant re-entry (approximates periodic rotation)."""

    def __init__(self):
        super().__init__("xsect_momentum")

    async def evaluate(self, symbol, candles_5m, candles_1h, market_ctx: MarketContext) -> Optional[Signal]:
        if market_ctx.regime == "TREND_DOWN":
            return None
        if len(candles_1h) < 20:
            return None

        quotes = market_ctx.quotes or {}
        rets = [q.pct_24h for q in quotes.values() if q and q.pct_24h is not None]
        if len(rets) < 8:
            return None

        q = quotes.get(symbol.upper())
        if not q or q.pct_24h is None or q.pct_24h <= 0:
            return None

        # Relative-strength rank: require TOP-5% 24h performer in the field
        rank_pct = sum(1 for r in rets if r <= q.pct_24h) / len(rets)
        if rank_pct < 0.95:
            return None

        closes_1h = [c.close for c in candles_1h]
        highs_1h = [c.high for c in candles_1h]
        price = closes_1h[-1]
        ema_20_1h = _ema(closes_1h, 20)
        if price <= ema_20_1h:
            return None

        # Require a FRESH continuation breakout (new 12h high) so we don't re-enter the
        # same strong names every scan — this is the main overtrading guard.
        prior_high = max(highs_1h[-13:-1])
        if not (price > prior_high and closes_1h[-2] <= prior_high):
            return None

        # Don't chase a parabolic 24h move (mean-reversion risk above ~30%)
        if q.pct_24h > 30.0:
            return None

        token = resolve(symbol)
        contract = token.contract if token else ""

        return Signal(
            symbol=symbol,
            contract=contract,
            side="buy",
            confidence=0.8,
            stop_loss_pct=3.0,
            take_profit_pct=8.0,   # let relative-strength winners run; monitor trails
            max_hold_min=600,
            rationale=f"Cross-sectional momentum: {symbol} is a top-decile 24h performer ({q.pct_24h:.1f}%, rank {rank_pct:.0%}), above 1h 20-EMA.",
            strategy_name=self.name,
        )
