"""Performance liquidation strategies — the moon-dev "Adaptive Percentile" family
(+ Cascade Filter, Volume-Confirmed, Burst Scalper), long/short perp.

The key idea: an ADAPTIVE percentile threshold instead of a fixed z-score. Fire
when the current move/liq magnitude exceeds the Nth percentile of its OWN recent
regime — self-calibrating per asset/period, which is far more robust across
unseen data and other assets than a hard 2.5σ gate. Top moon-dev performer.

Backtestable on the kline proxy (|return| percentile). Direction by flush sign,
regime-disciplined. All start DISABLED — gated through the robustness harness.
"""
from typing import Optional, List
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from data.tokens import resolve
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


def _abs_returns(closes: List[float]) -> List[float]:
    return [abs((closes[i] - closes[i - 1]) / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]


def percentile_rank(value: float, sample: List[float]) -> float:
    """Percent of sample <= value (0..100)."""
    if not sample:
        return 0.0
    return 100.0 * sum(1 for x in sample if x <= value) / len(sample)


def _adaptive_flush(closes: List[float]):
    """(flush_dir, pct_rank) for the latest bar's move vs its own recent regime,
    or None if insufficient history."""
    lb = int(getattr(settings, "adaptive_lookback", 100))
    if len(closes) < lb + 2:
        return None
    rets = _abs_returns(closes)
    if len(rets) < lb:
        return None
    cur = (closes[-1] - closes[-2]) / closes[-2] if closes[-2] else 0.0
    sample = rets[-(lb + 1):-1]              # exclude the current bar
    pr = percentile_rank(abs(cur), sample)
    return ("down" if cur < 0 else "up", pr)


# --- #5 Adaptive Percentile — REVERSION (the mean-rev the user asked for) ---
class AdaptivePercentileReversionPerp(BaseStrategy):
    def __init__(self): super().__init__("adaptive_percentile_reversion_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 110:
            return None
        f = _adaptive_flush([x.close for x in c1h])
        if not f:
            return None
        flush, pr = f
        if pr < float(getattr(settings, "adaptive_percentile", 95.0)):
            return None
        if flush == "down" and ctx.regime in ("TREND_UP", "CHOP"):
            return _mk(self.name, symbol, "long", 0.78, 3.0, 5.0, f"Adaptive-pctile {pr:.0f}th down flush -> fade long.")
        if flush == "up" and ctx.regime in ("TREND_DOWN", "RISK_OFF", "CHOP"):
            return _mk(self.name, symbol, "short", 0.78, 3.0, 5.0, f"Adaptive-pctile {pr:.0f}th up flush -> fade short.")
        return None


# --- #5 Adaptive Percentile — MOMENTUM (follow) ---
class AdaptivePercentileMomentumPerp(BaseStrategy):
    def __init__(self): super().__init__("adaptive_percentile_momentum_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 110:
            return None
        f = _adaptive_flush([x.close for x in c1h])
        if not f:
            return None
        flush, pr = f
        if pr < float(getattr(settings, "adaptive_percentile", 95.0)):
            return None
        if flush == "down" and ctx.regime in ("TREND_DOWN", "RISK_OFF", "CHOP"):
            return _mk(self.name, symbol, "short", 0.74, 3.0, 6.0, f"Adaptive-pctile {pr:.0f}th down flush -> momentum short.")
        if flush == "up" and ctx.regime in ("TREND_UP", "CHOP"):
            return _mk(self.name, symbol, "long", 0.74, 3.0, 6.0, f"Adaptive-pctile {pr:.0f}th up flush -> momentum long.")
        return None


# --- Cascade Filter: percentile + volume + not-overextended, FADE ---
class CascadeFilterPerp(BaseStrategy):
    def __init__(self): super().__init__("cascade_filter_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 110:
            return None
        closes = [x.close for x in c1h]; vols = [x.volume for x in c1h]
        f = _adaptive_flush(closes)
        if not f:
            return None
        flush, pr = f
        if pr < float(getattr(settings, "adaptive_percentile", 95.0)):
            return None
        avg = sum(vols[-21:-1]) / 20.0
        if avg <= 0 or vols[-1] < 1.5 * avg:        # volume filter
            return None
        if flush == "down" and ctx.regime in ("TREND_UP", "CHOP"):
            return _mk(self.name, symbol, "long", 0.78, 3.0, 5.0, f"Cascade-filtered down flush (pctile {pr:.0f}, vol {vols[-1]/avg:.1f}x) -> long.")
        if flush == "up" and ctx.regime in ("TREND_DOWN", "RISK_OFF", "CHOP"):
            return _mk(self.name, symbol, "short", 0.78, 3.0, 5.0, f"Cascade-filtered up flush (pctile {pr:.0f}, vol {vols[-1]/avg:.1f}x) -> short.")
        return None


# --- Volume-Confirmed Reversion ---
class VolumeConfirmedReversionPerp(BaseStrategy):
    def __init__(self): super().__init__("volume_confirmed_reversion_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 110:
            return None
        closes = [x.close for x in c1h]; vols = [x.volume for x in c1h]
        f = _adaptive_flush(closes)
        if not f:
            return None
        flush, pr = f
        if pr < max(50.0, float(getattr(settings, "adaptive_percentile", 95.0)) - 5.0):  # looser pctile, stronger volume gate
            return None
        avg = sum(vols[-21:-1]) / 20.0
        if avg <= 0 or vols[-1] < 2.0 * avg:
            return None
        if flush == "down" and ctx.regime in ("TREND_UP", "CHOP"):
            return _mk(self.name, symbol, "long", 0.76, 3.0, 5.0, f"Volume-confirmed down flush -> fade long.")
        if flush == "up" and ctx.regime in ("TREND_DOWN", "RISK_OFF", "CHOP"):
            return _mk(self.name, symbol, "short", 0.76, 3.0, 5.0, f"Volume-confirmed up flush -> fade short.")
        return None


# --- Burst Scalper: extreme single-bar burst, tight TP, short hold (fade) ---
class BurstScalperPerp(BaseStrategy):
    def __init__(self): super().__init__("burst_scalper_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 110:
            return None
        f = _adaptive_flush([x.close for x in c1h])
        if not f:
            return None
        flush, pr = f
        if pr < min(99.5, float(getattr(settings, "adaptive_percentile", 95.0)) + 3.0):  # only the most extreme bursts
            return None
        if flush == "down" and ctx.regime in ("TREND_UP", "CHOP"):
            return _mk(self.name, symbol, "long", 0.76, 2.0, 3.0, f"Burst scalp: {pr:.0f}th down burst -> quick long.", mh=180)
        if flush == "up" and ctx.regime in ("TREND_DOWN", "RISK_OFF", "CHOP"):
            return _mk(self.name, symbol, "short", 0.76, 2.0, 3.0, f"Burst scalp: {pr:.0f}th up burst -> quick short.", mh=180)
        return None


IDEAS = {
    "adaptive_percentile_reversion_perp": AdaptivePercentileReversionPerp,
    "adaptive_percentile_momentum_perp": AdaptivePercentileMomentumPerp,
    "cascade_filter_perp": CascadeFilterPerp,
    "volume_confirmed_reversion_perp": VolumeConfirmedReversionPerp,
    "burst_scalper_perp": BurstScalperPerp,
}
