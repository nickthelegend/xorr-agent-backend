"""Oscillator mean-reversion strategies ported from trader.dev (the top-Sharpe
strategies on our majors were ALL mean-reversion — independent confirmation of the
fade edge). Long/short perp, regime-disciplined. Distinct oscillators from our book
(we had RSI/Stochastic; these add TSI, Ultimate Oscillator, Aroon).

  tsi_mr_perp    — True Strength Index crossunder/crossover (trader.dev: TSI MR)
  uo_mr_perp     — Ultimate Oscillator oversold/overbought (UO 7/14/28)
  aroon_mr_perp  — Aroon Oscillator crossunder/crossover (Aroon 25)

trader.dev's individual Sharpes (5-6) are overfit (only 13-37 trades); ported here
properly and gated through OUR robustness gauntlet before enabling.
"""
from typing import Optional, List
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from strategies.macd_perp import _ema_series
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


def _fade_ok(direction, regime):
    if direction == "long":
        return regime in ("TREND_UP", "CHOP")
    return regime in ("TREND_DOWN", "RISK_OFF", "CHOP")


# --- True Strength Index series ---
def tsi_series(closes: List[float], r: int = 25, s: int = 13) -> List[float]:
    if len(closes) < r + s + 2:
        return []
    pc = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    apc = [abs(x) for x in pc]
    num = _ema_series(_ema_series(pc, r), s)
    den = _ema_series(_ema_series(apc, r), s)
    return [100.0 * num[i] / den[i] if den[i] != 0 else 0.0 for i in range(len(num))]


# --- Ultimate Oscillator series ---
def uo_series(candles, f=7, m=14, sl=28) -> List[float]:
    n = len(candles)
    if n < sl + 2:
        return []
    bp = [0.0] * n
    tr = [0.0] * n
    for i in range(1, n):
        c = candles[i]; pc = candles[i - 1].close
        bp[i] = c.close - min(c.low, pc)
        tr[i] = max(c.high, pc) - min(c.low, pc)
    out = []
    for i in range(sl, n):
        def avg(p):
            stp = sum(tr[i - p + 1:i + 1])
            return (sum(bp[i - p + 1:i + 1]) / stp) if stp > 0 else 0.0
        out.append(100.0 * (4 * avg(f) + 2 * avg(m) + avg(sl)) / 7.0)
    return out


# --- Aroon Oscillator (last two values) ---
def aroon_osc_last2(candles, period: int = 25):
    if len(candles) < period + 2:
        return None
    def osc(end):
        seg = candles[end - period:end]
        highs = [c.high for c in seg]; lows = [c.low for c in seg]
        hh = max(range(period), key=lambda k: highs[k])   # index of highest high
        ll = min(range(period), key=lambda k: lows[k])
        up = 100.0 * (hh + 1) / period          # bars since highest (recent = high idx)
        dn = 100.0 * (ll + 1) / period
        return up - dn
    return osc(len(candles) - 1), osc(len(candles))


class TsiMrPerp(BaseStrategy):
    def __init__(self): super().__init__("tsi_mr_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 45:
            return None
        t = tsi_series([x.close for x in c1h])
        if len(t) < 2:
            return None
        thr = float(getattr(settings, "tsi_entry_thresh", 25.0))
        prev, cur = t[-2], t[-1]
        if prev >= -thr and cur < -thr and _fade_ok("long", ctx.regime):
            return _mk(self.name, symbol, "long", 0.76, 3.0, 5.0, f"TSI crossunder -{thr:.0f} ({cur:.0f}) -> MR long.")
        if prev <= thr and cur > thr and _fade_ok("short", ctx.regime):
            return _mk(self.name, symbol, "short", 0.76, 3.0, 5.0, f"TSI crossover +{thr:.0f} ({cur:.0f}) -> MR short.")
        return None


class UoMrPerp(BaseStrategy):
    def __init__(self): super().__init__("uo_mr_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 32:
            return None
        u = uo_series(c1h)
        if len(u) < 2:
            return None
        prev, cur = u[-2], u[-1]
        os_thr = float(getattr(settings, "uo_oversold", 35.0)); ob_thr = float(getattr(settings, "uo_overbought", 65.0))
        if prev >= os_thr and cur < os_thr and _fade_ok("long", ctx.regime):
            return _mk(self.name, symbol, "long", 0.76, 3.0, 5.0, f"Ultimate Osc crossunder {os_thr:.0f} ({cur:.0f}) -> long.")
        if prev <= ob_thr and cur > ob_thr and _fade_ok("short", ctx.regime):
            return _mk(self.name, symbol, "short", 0.76, 3.0, 5.0, f"Ultimate Osc crossover {ob_thr:.0f} ({cur:.0f}) -> short.")
        return None


class AroonMrPerp(BaseStrategy):
    def __init__(self): super().__init__("aroon_mr_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 30:
            return None
        a = aroon_osc_last2(c1h, int(getattr(settings, "aroon_period", 25)))
        if not a:
            return None
        prev, cur = a
        thr = float(getattr(settings, "aroon_entry_thresh", 50.0))
        if prev >= -thr and cur < -thr and _fade_ok("long", ctx.regime):
            return _mk(self.name, symbol, "long", 0.74, 3.0, 5.0, f"Aroon Osc crossunder -{thr:.0f} -> MR long.")
        if prev <= thr and cur > thr and _fade_ok("short", ctx.regime):
            return _mk(self.name, symbol, "short", 0.74, 3.0, 5.0, f"Aroon Osc crossover +{thr:.0f} -> MR short.")
        return None


IDEAS = {
    "tsi_mr_perp": TsiMrPerp,
    "uo_mr_perp": UoMrPerp,
    "aroon_mr_perp": AroonMrPerp,
}
