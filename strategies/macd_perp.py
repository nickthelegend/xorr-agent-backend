"""MACD + liquidation strategies (moon-dev "momentum is the right way to trade
liquidations" set), long/short perp.

  macd_regime_perp        — MACD Dual-Mode Regime + Continuation Gate: long on a
                            bullish MACD cross in an uptrend, short on a bearish
                            cross in a downtrend. Pure momentum trend follower.
  liq_macd_momentum_perp  — the HEADLINE: follow a liquidation cascade ONLY when
                            MACD momentum confirms the cascade direction (pure
                            continuation got chopped; momentum-confirmed didn't).
  macd_liq_reversal_perp  — Big-Liq Reversal Fade: fade a LARGE cascade only when
                            MACD momentum is already turning back (exhaustion).

Liq-scaled sizing: the liq variants raise signal confidence with the cascade
z-score, so the perp margin (which scales with confidence) sizes up on bigger
forced-flow events — the "Liq-Scaled Sizing" idea, folded into the existing sizer.
"""
from typing import Optional, List
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from data.tokens import resolve
from data.cascade import detect_cascade
from config import settings


def _ema_series(vals: List[float], period: int) -> List[float]:
    k = 2.0 / (period + 1.0)
    out = [vals[0]]
    for v in vals[1:]:
        out.append((v - out[-1]) * k + out[-1])
    return out


def macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """Latest MACD state, or None if insufficient history."""
    if len(closes) < slow + signal + 1:
        return None
    ema_f = _ema_series(closes, fast)
    ema_s = _ema_series(closes, slow)
    line = [f - s for f, s in zip(ema_f, ema_s)]
    sig = _ema_series(line, signal)
    hist = [m - s for m, s in zip(line, sig)]
    return {"macd": line[-1], "signal": sig[-1], "hist": hist[-1],
            "hist_prev": hist[-2], "rising": hist[-1] > hist[-2],
            "cross_up": hist[-2] <= 0 < hist[-1], "cross_down": hist[-2] >= 0 > hist[-1]}


def _perp_symbols() -> set:
    raw = getattr(settings, "perp_symbols", "") or ""
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


def _mk(name, symbol, direction, lev, conf, sl, tp, rationale, max_hold=600) -> Signal:
    token = resolve(symbol)
    return Signal(symbol=symbol, contract=token.contract if token else "",
                  side="buy" if direction == "long" else "sell", confidence=conf,
                  stop_loss_pct=sl, take_profit_pct=tp, max_hold_min=max_hold,
                  rationale=rationale, strategy_name=name,
                  direction=direction, venue="perp", leverage=lev)


class MacdRegimePerpStrategy(BaseStrategy):
    """MACD Dual-Mode Regime + Continuation Gate."""
    def __init__(self):
        super().__init__("macd_regime_perp")

    async def evaluate(self, symbol, candles_5m, candles_1h, market_ctx: MarketContext) -> Optional[Signal]:
        if symbol.upper() not in _perp_symbols() or len(candles_1h) < 40:
            return None
        m = macd([c.close for c in candles_1h])
        if not m:
            return None
        lev = float(getattr(settings, "perp_leverage", 3.0))
        regime = market_ctx.regime
        # dual mode: long-mode in uptrend on a bullish cross; short-mode in downtrend
        if m["cross_up"] and m["hist"] > 0 and regime in ("TREND_UP", "CHOP"):
            return _mk(self.name, symbol, "long", lev, 0.74, 3.0, 6.0,
                       f"MACD bullish cross (hist {m['hist']:.4f}) in {regime}.")
        if m["cross_down"] and m["hist"] < 0 and regime in ("TREND_DOWN", "RISK_OFF"):
            return _mk(self.name, symbol, "short", lev, 0.74, 3.0, 6.0,
                       f"MACD bearish cross (hist {m['hist']:.4f}) in {regime}.")
        return None


class LiqMacdMomentumPerpStrategy(BaseStrategy):
    """Momentum-confirmed liquidation continuation (the headline idea)."""
    def __init__(self):
        super().__init__("liq_macd_momentum_perp")

    async def evaluate(self, symbol, candles_5m, candles_1h, market_ctx: MarketContext) -> Optional[Signal]:
        if symbol.upper() not in _perp_symbols() or len(candles_1h) < 40:
            return None
        c = detect_cascade(symbol, candles_1h)
        if not c or c["z"] < float(getattr(settings, "liq_z_threshold", 2.5)):
            return None
        m = macd([c2.close for c2 in candles_1h])
        if not m:
            return None
        lev = float(getattr(settings, "perp_leverage", 3.0))
        regime = market_ctx.regime
        # liq-scaled confidence: bigger cascade -> higher conviction -> bigger size
        conf = min(0.92, 0.70 + 0.04 * (c["z"] - 2.5))
        # FOLLOW the flush ONLY when MACD momentum agrees with it
        if c["flush_dir"] == "down" and m["hist"] < 0 and regime in ("TREND_DOWN", "RISK_OFF", "CHOP"):
            return _mk(self.name, symbol, "short", lev, conf, 3.0, 6.0,
                       f"Momentum-confirmed liq: down flush (z={c['z']:.1f}) + MACD bearish (hist {m['hist']:.4f}).")
        if c["flush_dir"] == "up" and m["hist"] > 0 and regime in ("TREND_UP", "CHOP"):
            return _mk(self.name, symbol, "long", lev, conf, 3.0, 6.0,
                       f"Momentum-confirmed liq: up flush (z={c['z']:.1f}) + MACD bullish (hist {m['hist']:.4f}).")
        return None


class MacdLiqReversalPerpStrategy(BaseStrategy):
    """Big-Liq Reversal Fade — fade a LARGE cascade only when MACD is turning back."""
    def __init__(self):
        super().__init__("macd_liq_reversal_perp")

    async def evaluate(self, symbol, candles_5m, candles_1h, market_ctx: MarketContext) -> Optional[Signal]:
        if symbol.upper() not in _perp_symbols() or len(candles_1h) < 40:
            return None
        c = detect_cascade(symbol, candles_1h)
        big = float(getattr(settings, "liq_big_z_threshold", 3.0))
        if not c or c["z"] < big:
            return None
        m = macd([c2.close for c2 in candles_1h])
        if not m:
            return None
        lev = float(getattr(settings, "perp_leverage", 3.0))
        regime = market_ctx.regime
        conf = min(0.92, 0.70 + 0.04 * (c["z"] - big))
        # FADE the flush only when MACD momentum is turning back the fade way
        if c["flush_dir"] == "down" and m["rising"] and regime in ("TREND_UP", "CHOP"):
            return _mk(self.name, symbol, "long", lev, conf, 3.0, 5.0,
                       f"Big-liq reversal FADE: down flush (z={c['z']:.1f}) + MACD turning up.")
        if c["flush_dir"] == "up" and not m["rising"] and regime in ("TREND_DOWN", "RISK_OFF", "CHOP"):
            return _mk(self.name, symbol, "short", lev, conf, 3.0, 5.0,
                       f"Big-liq reversal FADE: up flush (z={c['z']:.1f}) + MACD turning down.")
        return None
