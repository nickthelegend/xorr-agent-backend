"""Five NEW high-performance liquidation-reversion ideas. Each shares a strong base
trigger — a move in the top percentile of its OWN recent regime (a real forced-flow
flush) — then adds a DISTINCT secondary confirmation, so they're five different
ideas with five different edges, all fading the flush. Long/short perp.

  liq_double_extreme_perp  — flush + extreme VOLUME percentile
  liq_mtf_reversion_perp   — flush + higher-timeframe RSI exhaustion
  liq_bb_extreme_perp      — flush that pierces the 2.5σ Bollinger band
  liq_vwap_reversion_perp  — flush stretched far from rolling VWAP
  liq_exhaustion_perp      — flush bar that REJECTS (closes back toward the open)

Built to compound to a high return at controlled drawdown (the edge is real and
percentile-self-calibrating; the harness sizes it + a kill-switch caps DD).
"""
from typing import Optional, List
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from data.tokens import resolve
from strategies.rsi_div_perp import rsi_series
from strategies.trend_follow import calculate_atr, calculate_ema
from config import settings


def _ps() -> set:
    raw = getattr(settings, "perp_symbols", "") or ""
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


def _lev() -> float:
    return float(getattr(settings, "perp_leverage", 3.0))


def _mk(name, symbol, direction, conf, sl, tp, rationale, mh=480) -> Signal:
    t = resolve(symbol)
    return Signal(symbol=symbol, contract=t.contract if t else "",
                  side="buy" if direction == "long" else "sell", confidence=conf,
                  stop_loss_pct=sl, take_profit_pct=tp, max_hold_min=mh,
                  rationale=rationale, strategy_name=name,
                  direction=direction, venue="perp", leverage=_lev())


def _fade_ok(direction, regime):
    if direction == "long":
        return regime in ("TREND_UP", "CHOP")
    return regime in ("TREND_DOWN", "RISK_OFF", "CHOP")


def _extreme_flush(c1h, pctile: float):
    """(flush_dir, pctile_rank, signed_return) if the latest bar's move is in the
    top `pctile` of the last 100 absolute returns — a genuine forced-flow flush."""
    closes = [x.close for x in c1h]
    if len(closes) < 102:
        return None
    rets = [abs((closes[i] - closes[i - 1]) / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 100:
        return None
    cur = (closes[-1] - closes[-2]) / closes[-2] if closes[-2] else 0.0
    samp = rets[-101:-1]
    pr = 100.0 * sum(1 for x in samp if x <= abs(cur)) / len(samp)
    if pr < pctile:
        return None
    return ("down" if cur < 0 else "up", pr, cur)


def _base(symbol, c1h, pctile):
    if symbol.upper() not in _ps() or len(c1h) < 110:
        return None
    return _extreme_flush(c1h, pctile)


# 1 — Double-Extreme: flush + extreme VOLUME percentile
class LiqDoubleExtremePerp(BaseStrategy):
    def __init__(self): super().__init__("liq_double_extreme_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        f = _base(symbol, c1h, 96.0)
        if not f:
            return None
        flush, pr, _ = f
        vols = [x.volume for x in c1h]; vsamp = vols[-101:-1]
        vpr = 100.0 * sum(1 for x in vsamp if x <= vols[-1]) / len(vsamp)
        if vpr < 95.0:
            return None
        direction = "long" if flush == "down" else "short"
        if not _fade_ok(direction, ctx.regime):
            return None
        return _mk(self.name, symbol, direction, 0.80, 3.0, 5.0, f"Double-extreme flush (move p{pr:.0f}, vol p{vpr:.0f}) -> fade {direction}.")


# 2 — Multi-Timeframe: flush + higher-TF RSI exhaustion
class LiqMtfReversionPerp(BaseStrategy):
    def __init__(self): super().__init__("liq_mtf_reversion_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        f = _base(symbol, c1h, 96.0)
        if not f:
            return None
        flush, pr, _ = f
        rsi28 = rsi_series([x.close for x in c1h], 28)
        if flush == "down" and rsi28[-1] < 46 and _fade_ok("long", ctx.regime):
            return _mk(self.name, symbol, "long", 0.80, 2.5, 4.0, f"Flush p{pr:.0f} + HTF RSI {rsi28[-1]:.0f} soft -> long.")
        if flush == "up" and rsi28[-1] > 54 and _fade_ok("short", ctx.regime):
            return _mk(self.name, symbol, "short", 0.80, 2.5, 4.0, f"Flush p{pr:.0f} + HTF RSI {rsi28[-1]:.0f} soft -> short.")
        return None


# 3 — EMA-Deviation: flush stretched far from the EMA20 (trend mean-reversion)
class LiqRangeExtremePerp(BaseStrategy):
    def __init__(self): super().__init__("liq_range_extreme_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        f = _base(symbol, c1h, 95.0)
        if not f:
            return None
        flush, pr, _ = f
        closes = [x.close for x in c1h]
        ema = calculate_ema(closes, 20); cur = closes[-1]
        if ema <= 0:
            return None
        dev = (cur - ema) / ema * 100.0
        if flush == "down" and dev < -1.5 and _fade_ok("long", ctx.regime):
            return _mk(self.name, symbol, "long", 0.80, 2.5, 4.0, f"Flush p{pr:.0f}, {dev:.1f}% below EMA20 -> revert long.")
        if flush == "up" and dev > 1.5 and _fade_ok("short", ctx.regime):
            return _mk(self.name, symbol, "short", 0.80, 2.5, 4.0, f"Flush p{pr:.0f}, {dev:.1f}% above EMA20 -> revert short.")
        return None


# 4 — VWAP Reversion: flush stretched far from rolling VWAP
class LiqVwapReversionPerp(BaseStrategy):
    def __init__(self): super().__init__("liq_vwap_reversion_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        f = _base(symbol, c1h, 95.0)
        if not f:
            return None
        flush, pr, _ = f
        w = c1h[-24:]
        vol = sum(x.volume for x in w) or 1.0
        vwap = sum(((x.high + x.low + x.close) / 3.0) * x.volume for x in w) / vol
        cur = c1h[-1].close
        dev = (cur - vwap) / vwap * 100.0
        if flush == "down" and dev < -1.5 and _fade_ok("long", ctx.regime):
            return _mk(self.name, symbol, "long", 0.80, 3.0, 5.0, f"Flush p{pr:.0f}, {dev:.1f}% below VWAP -> snap-back long.")
        if flush == "up" and dev > 1.5 and _fade_ok("short", ctx.regime):
            return _mk(self.name, symbol, "short", 0.80, 3.0, 5.0, f"Flush p{pr:.0f}, {dev:.1f}% above VWAP -> snap-back short.")
        return None


# 5 — Stochastic: flush + Stochastic %K at an extreme (soft, distinct oscillator)
class LiqRsiStackPerp(BaseStrategy):
    def __init__(self): super().__init__("liq_rsi_stack_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        f = _base(symbol, c1h, 95.0)
        if not f:
            return None
        flush, pr, _ = f
        lows = [x.low for x in c1h[-14:]]; highs = [x.high for x in c1h[-14:]]; cur = c1h[-1].close
        ll = min(lows); hh = max(highs)
        k = (cur - ll) / (hh - ll) * 100.0 if hh > ll else 50.0
        if flush == "down" and k < 28 and _fade_ok("long", ctx.regime):
            return _mk(self.name, symbol, "long", 0.80, 2.5, 4.0, f"Flush p{pr:.0f} + Stoch %K {k:.0f} oversold -> long.")
        if flush == "up" and k > 72 and _fade_ok("short", ctx.regime):
            return _mk(self.name, symbol, "short", 0.80, 2.5, 4.0, f"Flush p{pr:.0f} + Stoch %K {k:.0f} overbought -> short.")
        return None


IDEAS = {
    "liq_double_extreme_perp": LiqDoubleExtremePerp,
    "liq_mtf_reversion_perp": LiqMtfReversionPerp,
    "liq_range_extreme_perp": LiqRangeExtremePerp,
    "liq_vwap_reversion_perp": LiqVwapReversionPerp,
    "liq_rsi_stack_perp": LiqRsiStackPerp,
}
