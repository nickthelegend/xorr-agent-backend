"""Salamander — pullback catcher (senpi archetype), long/short perp.

Buy dips in uptrends, short rallies in downtrends:
  LONG  when the symbol's trend is UP (price > 1h 50-EMA, EMA rising) AND price has
        pulled back 3-7% from the recent 24-bar high.
  SHORT when the trend is DOWN (price < 1h 50-EMA, EMA falling) AND price has
        rallied 3-7% from the recent 24-bar low.
The 3-7% band is the sweet spot — <3% is noise, >7% risks a trend break. Pullbacks
in an established trend are one of the highest-edge setups: the trend already
proved itself and the counter-move gives a better entry. Executed as a perp.
"""
from typing import Optional, List
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from strategies.trend_follow import calculate_ema
from data.tokens import resolve
from config import settings


class SalamanderPerpStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("salamander_perp")

    @staticmethod
    def _perp_symbols() -> set:
        raw = getattr(settings, "perp_symbols", "") or ""
        return {s.strip().upper() for s in raw.split(",") if s.strip()}

    async def evaluate(self, symbol, candles_5m, candles_1h, market_ctx: MarketContext) -> Optional[Signal]:
        if symbol.upper() not in self._perp_symbols():
            return None
        if len(candles_1h) < 55:
            return None

        leverage = float(getattr(settings, "perp_leverage", 3.0))
        regime = market_ctx.regime
        closes = [c.close for c in candles_1h]
        cur = closes[-1]

        ema50 = calculate_ema(closes, 50)
        ema50_prev = calculate_ema(closes[:-4], 50) if len(closes) > 54 else ema50
        rising = ema50 > ema50_prev
        falling = ema50 < ema50_prev

        # trend gate (symbol's own trend), aligned with the broad regime
        trend_up = cur > ema50 and rising and regime in ("TREND_UP", "CHOP")
        trend_down = cur < ema50 and falling and regime in ("TREND_DOWN", "RISK_OFF")

        recent = candles_1h[-25:-1]
        hi = max(c.high for c in recent)
        lo = min(c.low for c in recent)

        lo_band = float(getattr(settings, "salamander_pullback_min", 3.0))
        hi_band = float(getattr(settings, "salamander_pullback_max", 7.0))

        token = resolve(symbol)
        contract = token.contract if token else ""

        # LONG: pulled back 3-7% below the recent high while uptrend intact
        if trend_up and hi > 0:
            pullback = (hi - cur) / hi * 100.0
            if lo_band <= pullback <= hi_band:
                return Signal(symbol=symbol, contract=contract, side="buy", confidence=0.78,
                              stop_loss_pct=3.5, take_profit_pct=7.0, max_hold_min=720,
                              rationale=f"Salamander LONG: {pullback:.1f}% dip in an uptrend (px ${cur:.4f} vs 24h high ${hi:.4f}).",
                              strategy_name=self.name, direction="long", venue="perp", leverage=leverage)

        # SHORT: rallied 3-7% above the recent low while downtrend intact
        if trend_down and lo > 0:
            rally = (cur - lo) / lo * 100.0
            if lo_band <= rally <= hi_band:
                return Signal(symbol=symbol, contract=contract, side="sell", confidence=0.78,
                              stop_loss_pct=3.5, take_profit_pct=7.0, max_hold_min=720,
                              rationale=f"Salamander SHORT: {rally:.1f}% rally in a downtrend (px ${cur:.4f} vs 24h low ${lo:.4f}).",
                              strategy_name=self.name, direction="short", venue="perp", leverage=leverage)
        return None
