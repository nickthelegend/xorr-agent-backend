"""RSI-divergence long/short perp — one of the most-referenced crypto setups
(multiple repos in the list cite ~65% win on crypto).

Bullish divergence: price makes a LOWER low but RSI makes a HIGHER low (selling
exhausting) -> LONG. Bearish divergence: price makes a HIGHER high but RSI makes
a LOWER high -> SHORT. Non-repainting: evaluated only on closed bars, comparing a
recent pivot to the prior-window pivot. Regime-gated.
"""
from typing import Optional, List
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from data.tokens import resolve
from config import settings


def rsi_series(closes: List[float], period: int = 14) -> List[float]:
    """RSI aligned to closes (front-padded with 50.0 until enough data)."""
    n = len(closes)
    out = [50.0] * n
    if n < period + 1:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains[i] = max(d, 0.0)
        losses[i] = max(-d, 0.0)
    avg_g = sum(gains[1:period + 1]) / period
    avg_l = sum(losses[1:period + 1]) / period
    for i in range(period, n):
        if i > period:
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
        rs = (avg_g / avg_l) if avg_l > 0 else 999.0
        out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


class RsiDivPerpStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("rsi_div_perp")

    @staticmethod
    def _perp_symbols() -> set:
        raw = getattr(settings, "perp_symbols", "") or ""
        return {s.strip().upper() for s in raw.split(",") if s.strip()}

    async def evaluate(self, symbol, candles_5m, candles_1h, market_ctx: MarketContext) -> Optional[Signal]:
        if symbol.upper() not in self._perp_symbols():
            return None
        lb = int(getattr(settings, "rsi_div_lookback", 20))
        if len(candles_1h) < lb + 4:
            return None

        leverage = float(getattr(settings, "perp_leverage", 3.0))
        regime = market_ctx.regime
        closes = [c.close for c in candles_1h]
        lows = [c.low for c in candles_1h]
        highs = [c.high for c in candles_1h]
        rsi = rsi_series(closes, 14)
        cur = closes[-1]

        # recent pivot = last 3 bars; prior pivot = the lb-bar window before that
        recent = slice(len(closes) - 3, len(closes))
        prior = slice(len(closes) - 3 - lb, len(closes) - 3)

        allow_long = regime in ("TREND_UP", "CHOP")
        allow_short = regime in ("TREND_DOWN", "RISK_OFF")

        token = resolve(symbol)
        contract = token.contract if token else ""

        # Bullish divergence -> LONG
        r_lo_idx = min(range(recent.start, recent.stop), key=lambda i: lows[i])
        p_lo_idx = min(range(prior.start, prior.stop), key=lambda i: lows[i])
        if allow_long and lows[r_lo_idx] < lows[p_lo_idx] and rsi[r_lo_idx] > rsi[p_lo_idx] and rsi[r_lo_idx] < 45:
            return Signal(symbol=symbol, contract=contract, side="buy", confidence=0.74,
                          stop_loss_pct=3.0, take_profit_pct=6.0, max_hold_min=600,
                          rationale=f"Bullish RSI divergence: price LL ${lows[r_lo_idx]:.4f}<{lows[p_lo_idx]:.4f} but RSI HL {rsi[r_lo_idx]:.0f}>{rsi[p_lo_idx]:.0f}.",
                          strategy_name=self.name, direction="long", venue="perp", leverage=leverage)

        # Bearish divergence -> SHORT
        r_hi_idx = max(range(recent.start, recent.stop), key=lambda i: highs[i])
        p_hi_idx = max(range(prior.start, prior.stop), key=lambda i: highs[i])
        if allow_short and highs[r_hi_idx] > highs[p_hi_idx] and rsi[r_hi_idx] < rsi[p_hi_idx] and rsi[r_hi_idx] > 55:
            return Signal(symbol=symbol, contract=contract, side="sell", confidence=0.74,
                          stop_loss_pct=3.0, take_profit_pct=6.0, max_hold_min=600,
                          rationale=f"Bearish RSI divergence: price HH ${highs[r_hi_idx]:.4f}>{highs[p_hi_idx]:.4f} but RSI LH {rsi[r_hi_idx]:.0f}<{rsi[p_hi_idx]:.0f}.",
                          strategy_name=self.name, direction="short", venue="perp", leverage=leverage)
        return None
