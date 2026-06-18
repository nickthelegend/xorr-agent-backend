"""Volatility-squeeze breakout (geektrade/TTM-squeeze archetype), long/short perp.

A "squeeze" = Bollinger Bands contracting INSIDE the Keltner Channel (volatility
compressed). When the squeeze RELEASES on a volume spike, price tends to expand
hard in the breakout direction. Published geektrade params: BB(20,2) inside
KC(20,1.5), volume spike >=1.8x, stop 2.0xATR, target 3.5xATR. Regime-gated.
"""
from typing import Optional, List
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from strategies.trend_follow import calculate_ema, calculate_atr
from data.tokens import resolve
from config import settings


def _sma(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: List[float], mean: float) -> float:
    if len(xs) < 2:
        return 0.0
    var = sum((x - mean) ** 2 for x in xs) / len(xs)
    return var ** 0.5


class VolSqueezePerpStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("volsqueeze_perp")

    @staticmethod
    def _perp_symbols() -> set:
        raw = getattr(settings, "perp_symbols", "") or ""
        return {s.strip().upper() for s in raw.split(",") if s.strip()}

    def _squeeze_on(self, closes, candles, length, bb_mult, kc_mult) -> bool:
        window = closes[-length:]
        basis = _sma(window)
        dev = bb_mult * _stdev(window, basis)
        bb_u, bb_l = basis + dev, basis - dev
        ema = calculate_ema(closes, length)
        atr = calculate_atr(candles[-(length + 1):], length)
        kc_u, kc_l = ema + kc_mult * atr, ema - kc_mult * atr
        return bb_u < kc_u and bb_l > kc_l

    async def evaluate(self, symbol, candles_5m, candles_1h, market_ctx: MarketContext) -> Optional[Signal]:
        if symbol.upper() not in self._perp_symbols():
            return None
        length = int(getattr(settings, "volsq_len", 20))
        if len(candles_1h) < length + 3:
            return None

        leverage = float(getattr(settings, "perp_leverage", 3.0))
        regime = market_ctx.regime
        bb_mult = float(getattr(settings, "volsq_bb_mult", 2.0))
        kc_mult = float(getattr(settings, "volsq_kc_mult", 1.5))
        vol_spike = float(getattr(settings, "volsq_vol_spike", 1.8))
        sl_atr = float(getattr(settings, "volsq_sl_atr", 2.0))
        tp_atr = float(getattr(settings, "volsq_tp_atr", 3.5))

        closes = [c.close for c in candles_1h]
        vols = [c.volume for c in candles_1h]
        cur = closes[-1]

        # Squeeze must have RELEASED this bar (was compressed, now expanding)
        on_prev = self._squeeze_on(closes[:-1], candles_1h[:-1], length, bb_mult, kc_mult)
        on_now = self._squeeze_on(closes, candles_1h, length, bb_mult, kc_mult)
        if not (on_prev and not on_now):
            return None

        avg_vol = sum(vols[-(length + 1):-1]) / length
        if avg_vol <= 0 or vols[-1] < vol_spike * avg_vol:
            return None

        basis = _sma(closes[-length:])
        atr = calculate_atr(candles_1h[-(length + 1):], length)
        if atr <= 0:
            return None

        # Direction = breakout side relative to the squeeze basis
        going_long = cur > basis
        allow_long = regime in ("TREND_UP", "CHOP")
        allow_short = regime in ("TREND_DOWN", "RISK_OFF")

        token = resolve(symbol)
        contract = token.contract if token else ""
        sl_pct = max(1.5, min(5.0, sl_atr * atr / cur * 100.0))
        tp_pct = max(3.0, min(12.0, tp_atr * atr / cur * 100.0))

        if going_long and allow_long:
            return Signal(symbol=symbol, contract=contract, side="buy", confidence=0.76,
                          stop_loss_pct=round(sl_pct, 2), take_profit_pct=round(tp_pct, 2), max_hold_min=720,
                          rationale=f"Vol-squeeze release LONG at ${cur:.4f} on {vols[-1]/avg_vol:.1f}x volume ({regime}).",
                          strategy_name=self.name, direction="long", venue="perp", leverage=leverage)
        if (not going_long) and allow_short:
            return Signal(symbol=symbol, contract=contract, side="sell", confidence=0.76,
                          stop_loss_pct=round(sl_pct, 2), take_profit_pct=round(tp_pct, 2), max_hold_min=720,
                          rationale=f"Vol-squeeze release SHORT at ${cur:.4f} on {vols[-1]/avg_vol:.1f}x volume ({regime}).",
                          strategy_name=self.name, direction="short", venue="perp", leverage=leverage)
        return None
