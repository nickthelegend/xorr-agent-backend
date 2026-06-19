"""More mean-reversion oscillators — distinct archetypes we didn't already have, all
long-spot-viable (buy the oversold flush, sell the bounce). Symmetric + regime-gated so
they work in both venues; spot takes the long side only.

  cci_mr_perp        CCI(20) crossunder -100 (oversold flush) -> long
  williams_mr_perp   Williams %R(14) crossunder -80           -> long
  bb_bounce_perp     close tags BELOW the lower Bollinger band -> long  (band touch, not breakout)
  mfi_mr_perp        Money Flow Index(14) crossunder 20        -> long  (volume-weighted oversold)

Gated through the --spot backtest before enabling, same as every other strategy.
"""
from typing import List, Optional
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from data.tokens import resolve
from config import settings


def _ps() -> set:
    raw = getattr(settings, "perp_symbols", "") or ""
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


def _lev() -> float:
    return float(getattr(settings, "perp_leverage", 3.0))


def _mk(name, symbol, direction, conf, sl, tp, rationale) -> Signal:
    t = resolve(symbol)
    return Signal(symbol=symbol, contract=t.contract if t else "",
                  side="buy" if direction == "long" else "sell", confidence=conf,
                  stop_loss_pct=sl, take_profit_pct=tp, max_hold_min=480,
                  rationale=rationale, strategy_name=name,
                  direction=direction, venue="perp", leverage=_lev())


def _dir_ok(direction, regime):
    if direction == "long":
        return regime in ("TREND_UP", "CHOP")
    return regime in ("TREND_DOWN", "RISK_OFF", "CHOP")


# --- indicators (return the last two values for crossunder/crossover detection) ---
def cci_last2(candles, n: int = 20):
    if len(candles) < n + 2:
        return None
    tp = [(c.high + c.low + c.close) / 3.0 for c in candles]

    def at(end):
        seg = tp[end - n:end]
        sma = sum(seg) / n
        md = sum(abs(x - sma) for x in seg) / n
        return (tp[end - 1] - sma) / (0.015 * md) if md > 0 else 0.0
    return at(len(candles) - 1), at(len(candles))


def williams_last2(candles, n: int = 14):
    if len(candles) < n + 2:
        return None

    def at(end):
        seg = candles[end - n:end]
        hh = max(c.high for c in seg)
        ll = min(c.low for c in seg)
        c = candles[end - 1].close
        return -100.0 * (hh - c) / (hh - ll) if hh > ll else -50.0
    return at(len(candles) - 1), at(len(candles))


def mfi_last2(candles, n: int = 14):
    if len(candles) < n + 3:
        return None
    tp = [(c.high + c.low + c.close) / 3.0 for c in candles]
    rmf = [tp[i] * candles[i].volume for i in range(len(candles))]

    def at(end):
        pos = neg = 0.0
        for i in range(end - n, end):
            if tp[i] > tp[i - 1]:
                pos += rmf[i]
            elif tp[i] < tp[i - 1]:
                neg += rmf[i]
        if neg == 0:
            return 100.0
        ratio = pos / neg
        return 100.0 - 100.0 / (1.0 + ratio)
    return at(len(candles) - 1), at(len(candles))


def _bb(closes, n=20, mult=2.0):
    seg = closes[-n:]
    basis = sum(seg) / n
    std = (sum((x - basis) ** 2 for x in seg) / n) ** 0.5
    return basis, basis + mult * std, basis - mult * std


class CciMrPerp(BaseStrategy):
    def __init__(self): super().__init__("cci_mr_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 30:
            return None
        v = cci_last2(c1h, int(getattr(settings, "cci_period", 20)))
        if not v:
            return None
        thr = float(getattr(settings, "cci_thresh", 100.0))
        prev, cur = v
        if prev >= -thr and cur < -thr and _dir_ok("long", ctx.regime):
            return _mk(self.name, symbol, "long", 0.75, 3.5, 5.5, f"CCI crossunder -{thr:.0f} ({cur:.0f}) -> MR long.")
        if prev <= thr and cur > thr and _dir_ok("short", ctx.regime):
            return _mk(self.name, symbol, "short", 0.75, 3.5, 5.5, f"CCI crossover +{thr:.0f} ({cur:.0f}) -> MR short.")
        return None


class WilliamsMrPerp(BaseStrategy):
    def __init__(self): super().__init__("williams_mr_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 25:
            return None
        v = williams_last2(c1h, int(getattr(settings, "williams_period", 14)))
        if not v:
            return None
        os_t = float(getattr(settings, "williams_oversold", -80.0))
        ob_t = float(getattr(settings, "williams_overbought", -20.0))
        prev, cur = v
        if prev >= os_t and cur < os_t and _dir_ok("long", ctx.regime):
            return _mk(self.name, symbol, "long", 0.74, 3.5, 5.0, f"Williams %R crossunder {os_t:.0f} -> long.")
        if prev <= ob_t and cur > ob_t and _dir_ok("short", ctx.regime):
            return _mk(self.name, symbol, "short", 0.74, 3.5, 5.0, f"Williams %R crossover {ob_t:.0f} -> short.")
        return None


class BbBouncePerp(BaseStrategy):
    """Band TOUCH reversion (not breakout): price tags below the lower Bollinger band
    in a non-trending tape -> buy the snap-back to the basis."""
    def __init__(self): super().__init__("bb_bounce_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 30:
            return None
        closes = [x.close for x in c1h]
        n = int(getattr(settings, "bb_bounce_len", 20))
        mult = float(getattr(settings, "bb_bounce_mult", 2.0))
        basis, upper, lower = _bb(closes, n, mult)
        prev_c, cur_c = closes[-2], closes[-1]
        if prev_c >= lower and cur_c < lower and _dir_ok("long", ctx.regime):
            return _mk(self.name, symbol, "long", 0.75, 3.5, 5.0, "Tagged lower Bollinger band -> bounce long.")
        if prev_c <= upper and cur_c > upper and _dir_ok("short", ctx.regime):
            return _mk(self.name, symbol, "short", 0.75, 3.5, 5.0, "Tagged upper Bollinger band -> fade short.")
        return None


class MfiMrPerp(BaseStrategy):
    def __init__(self): super().__init__("mfi_mr_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 25:
            return None
        v = mfi_last2(c1h, int(getattr(settings, "mfi_period", 14)))
        if not v:
            return None
        os_t = float(getattr(settings, "mfi_oversold", 20.0))
        ob_t = float(getattr(settings, "mfi_overbought", 80.0))
        prev, cur = v
        if prev >= os_t and cur < os_t and _dir_ok("long", ctx.regime):
            return _mk(self.name, symbol, "long", 0.74, 3.5, 5.0, f"MFI crossunder {os_t:.0f} ({cur:.0f}) -> long.")
        if prev <= ob_t and cur > ob_t and _dir_ok("short", ctx.regime):
            return _mk(self.name, symbol, "short", 0.74, 3.5, 5.0, f"MFI crossover {ob_t:.0f} ({cur:.0f}) -> short.")
        return None


IDEAS = {
    "cci_mr_perp": CciMrPerp,
    "williams_mr_perp": WilliamsMrPerp,
    "bb_bounce_perp": BbBouncePerp,
    "mfi_mr_perp": MfiMrPerp,
}
