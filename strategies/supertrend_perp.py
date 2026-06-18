"""Supertrend (ATR) long/short perp strategy — the single most-popular TradingView
trend indicator (Period 10 / Multiplier 3). Researched + ported natively so it
backtests on the same harness as the rest of the book.

Logic: the Supertrend flips to UP when close clears the upper ATR band and to
DOWN when it breaks the lower band. We take the FRESH flip as the entry, in the
direction of the flip — but only when it agrees with the BTC regime, because (like
every trend signal) Supertrend whipsaws in CHOP. Executed as a leveraged perp.
"""
from typing import Optional, List
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from strategies.trend_follow import calculate_ema
from data.tokens import resolve
from config import settings


def supertrend_dir(candles, period: int = 10, mult: float = 3.0) -> List[int]:
    """Returns the Supertrend direction series (+1 up / -1 down) per bar."""
    n = len(candles)
    if n < period + 2:
        return []
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]
    trs = [0.0] * n
    for i in range(1, n):
        trs[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr = [0.0] * n
    for i in range(period, n):
        atr[i] = sum(trs[i - period + 1:i + 1]) / period
    hl2 = [(highs[i] + lows[i]) / 2.0 for i in range(n)]
    upper = [0.0] * n
    lower = [0.0] * n
    trend = [1] * n
    for i in range(period, n):
        bu = hl2[i] + mult * atr[i]
        bl = hl2[i] - mult * atr[i]
        upper[i] = bu if (i == period or bu < upper[i - 1] or closes[i - 1] > upper[i - 1]) else upper[i - 1]
        lower[i] = bl if (i == period or bl > lower[i - 1] or closes[i - 1] < lower[i - 1]) else lower[i - 1]
        if i == period:
            trend[i] = 1 if closes[i] >= hl2[i] else -1
        elif trend[i - 1] == 1:
            trend[i] = -1 if closes[i] < lower[i] else 1
        else:
            trend[i] = 1 if closes[i] > upper[i] else -1
    return trend


class SupertrendPerpStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("supertrend_perp")

    @staticmethod
    def _perp_symbols() -> set:
        raw = getattr(settings, "perp_symbols", "") or ""
        return {s.strip().upper() for s in raw.split(",") if s.strip()}

    async def evaluate(self, symbol, candles_5m, candles_1h, market_ctx: MarketContext) -> Optional[Signal]:
        if symbol.upper() not in self._perp_symbols():
            return None
        if len(candles_1h) < 25:
            return None

        leverage = float(getattr(settings, "perp_leverage", 3.0))
        regime = market_ctx.regime
        period = int(getattr(settings, "supertrend_period", 10))
        mult = float(getattr(settings, "supertrend_mult", 3.0))

        trend = supertrend_dir(candles_1h, period, mult)
        if len(trend) < 2:
            return None
        flipped_up = trend[-1] == 1 and trend[-2] == -1
        flipped_down = trend[-1] == -1 and trend[-2] == 1
        if not (flipped_up or flipped_down):
            return None

        # Regime discipline (same lesson as donchian_perp): only trade flips WITH the tape.
        allow_long = regime in ("TREND_UP", "CHOP")
        allow_short = regime in ("TREND_DOWN", "RISK_OFF")
        closes = [c.close for c in candles_1h]
        cur = closes[-1]

        token = resolve(symbol)
        contract = token.contract if token else ""

        if flipped_up and allow_long:
            return Signal(symbol=symbol, contract=contract, side="buy", confidence=0.76,
                          stop_loss_pct=3.0, take_profit_pct=7.0, max_hold_min=720,
                          rationale=f"Supertrend({period},{mult}) flipped UP at ${cur:.4f} in {regime}.",
                          strategy_name=self.name, direction="long", venue="perp", leverage=leverage)
        if flipped_down and allow_short:
            return Signal(symbol=symbol, contract=contract, side="sell", confidence=0.76,
                          stop_loss_pct=3.0, take_profit_pct=7.0, max_hold_min=720,
                          rationale=f"Supertrend({period},{mult}) flipped DOWN at ${cur:.4f} in {regime}.",
                          strategy_name=self.name, direction="short", venue="perp", leverage=leverage)
        return None
