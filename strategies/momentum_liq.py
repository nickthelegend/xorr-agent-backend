"""moon-dev momentum set — exact mechanics from the screenshots, long/short perp.

  cascade_consec_perp        — Cascade Filter: N consecutive same-direction bars
  zscore_advol_perp          — Z-Score Momentum: return / adaptive (rolling) vol
  volume_momentum_perp       — Volume-Confirmed Momentum: strong dir bar + volume
  dominant_burst_perp        — Dominant-Side Burst Scalper: liq imbalance >= 5x
  adaptive_p99_momentum_perp — Adaptive Percentile (rolling p99), FOLLOW

These are continuation/momentum plays. On the 1h kline-PROXY they tend to fail the
OOS gauntlet (momentum on the majors gets chopped); the dominant-burst one needs
the REAL liquidation imbalance (live feed) to express its edge. All start DISABLED
-> the robustness gauntlet + shadow system decide.
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


def _mk(name, symbol, direction, conf, sl, tp, rationale, mh=360) -> Signal:
    t = resolve(symbol)
    return Signal(symbol=symbol, contract=t.contract if t else "",
                  side="buy" if direction == "long" else "sell", confidence=conf,
                  stop_loss_pct=sl, take_profit_pct=tp, max_hold_min=mh,
                  rationale=rationale, strategy_name=name,
                  direction=direction, venue="perp", leverage=_lev())


def _follow_ok(direction, regime):
    if direction == "long":
        return regime in ("TREND_UP", "CHOP")
    return regime in ("TREND_DOWN", "RISK_OFF", "CHOP")


# 1 — Cascade Filter: N consecutive same-direction bars -> follow the cascade
class CascadeConsecPerp(BaseStrategy):
    def __init__(self): super().__init__("cascade_consec_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 30:
            return None
        n = int(getattr(settings, "consec_bars", 4))
        closes = [x.close for x in c1h]
        dirs = [1 if closes[i] > closes[i - 1] else -1 for i in range(len(closes) - n, len(closes))]
        if all(d == 1 for d in dirs):
            direction = "long"
        elif all(d == -1 for d in dirs):
            direction = "short"
        else:
            return None
        if not _follow_ok(direction, ctx.regime):
            return None
        return _mk(self.name, symbol, direction, 0.74, 3.0, 6.0, f"{n} consecutive {direction} bars -> momentum.")


# 2 — Z-Score Momentum (adaptive vol)
class ZscoreAdvolPerp(BaseStrategy):
    def __init__(self): super().__init__("zscore_advol_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 55:
            return None
        closes = [x.close for x in c1h]
        rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1] > 0]
        if len(rets) < 50:
            return None
        recent = rets[-50:-1]
        mean = sum(recent) / len(recent)
        std = (sum((x - mean) ** 2 for x in recent) / len(recent)) ** 0.5
        if std <= 0:
            return None
        z = (rets[-1] - mean) / std
        if abs(z) < float(getattr(settings, "liq_z_threshold", 2.5)):
            return None
        direction = "long" if z > 0 else "short"
        if not _follow_ok(direction, ctx.regime):
            return None
        return _mk(self.name, symbol, direction, 0.74, 3.0, 6.0, f"Adaptive-vol z-momentum z={z:+.1f} -> {direction}.")


# 3 — Volume-Confirmed Momentum
class VolumeMomentumPerp(BaseStrategy):
    def __init__(self): super().__init__("volume_momentum_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 30:
            return None
        closes = [x.close for x in c1h]; vols = [x.volume for x in c1h]
        r = (closes[-1] - closes[-2]) / closes[-2] if closes[-2] else 0.0
        avg = sum(vols[-21:-1]) / 20.0
        if avg <= 0 or vols[-1] < 2.0 * avg or abs(r) < 0.015:   # >=1.5% bar on >=2x vol
            return None
        direction = "long" if r > 0 else "short"
        if not _follow_ok(direction, ctx.regime):
            return None
        return _mk(self.name, symbol, direction, 0.74, 3.0, 6.0, f"Volume-confirmed momentum ({r*100:+.1f}% on {vols[-1]/avg:.1f}x) -> {direction}.")


# 4 — Dominant-Side Burst Scalper (5x liq imbalance)
class DominantBurstPerp(BaseStrategy):
    def __init__(self): super().__init__("dominant_burst_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 30:
            return None
        ratio = float(getattr(settings, "burst_imbalance_ratio", 5.0))
        direction = None
        # Prefer REAL liq imbalance (long_liq vs short_liq) from the live feed.
        try:
            from data import liq_feed
            m = liq_feed.liq_metrics(symbol)
            if m and m.get("total_usd", 0) > 0:
                # imbalance in [-1,1]; |imb| high => one side dominates. ratio r = (1+imb)/(1-imb)
                imb = m["imbalance"]
                denom = (1 - abs(imb)) or 1e-9
                if (1 + abs(imb)) / denom >= ratio:
                    # dominant LONG liqs (down flush) -> scalp the bounce LONG; mirror for up
                    direction = "long" if m["flush_dir"] == "down" else "short"
        except Exception:
            pass
        if direction is None:
            # PROXY: a bar whose body dwarfs the recent average range (one-sided burst)
            bodies = [abs(c1h[i].close - c1h[i].open) for i in range(len(c1h) - 21, len(c1h) - 1)]
            avg_body = sum(bodies) / len(bodies) if bodies else 0.0
            bar = c1h[-1]; body = abs(bar.close - bar.open)
            if avg_body <= 0 or body < ratio * avg_body:
                return None
            # burst scalper FADES the one-sided burst (snap-back)
            direction = "long" if bar.close < bar.open else "short"
        # fade gating (scalp the snap-back)
        if direction == "long" and ctx.regime not in ("TREND_UP", "CHOP"):
            return None
        if direction == "short" and ctx.regime not in ("TREND_DOWN", "RISK_OFF", "CHOP"):
            return None
        return _mk(self.name, symbol, direction, 0.74, 2.0, 3.0, f"Dominant-side {ratio:.0f}x burst -> scalp {direction}.", mh=180)


# 5 — Adaptive Percentile (rolling p99) — FOLLOW
class AdaptiveP99MomentumPerp(BaseStrategy):
    def __init__(self): super().__init__("adaptive_p99_momentum_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 110:
            return None
        closes = [x.close for x in c1h]
        rets_abs = [abs((closes[i] - closes[i - 1]) / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
        if len(rets_abs) < 100:
            return None
        cur = (closes[-1] - closes[-2]) / closes[-2] if closes[-2] else 0.0
        sample = rets_abs[-101:-1]
        pr = 100.0 * sum(1 for x in sample if x <= abs(cur)) / len(sample)
        if pr < 99.0:                          # rolling p99
            return None
        direction = "long" if cur > 0 else "short"
        if not _follow_ok(direction, ctx.regime):
            return None
        return _mk(self.name, symbol, direction, 0.74, 3.0, 6.0, f"Adaptive p99 ({pr:.0f}th) move -> momentum {direction}.")


IDEAS = {
    "cascade_consec_perp": CascadeConsecPerp,
    "zscore_advol_perp": ZscoreAdvolPerp,
    "volume_momentum_perp": VolumeMomentumPerp,
    "dominant_burst_perp": DominantBurstPerp,
    "adaptive_p99_momentum_perp": AdaptiveP99MomentumPerp,
}
