"""Liquidation-flow perp strategies (moon-dev / piranha class), long/short.

Three archetypes, all driven by cascade detection (real liq feed live, kline proxy
for backtest), all regime-disciplined and shadow-tested by default:

  liq_reversion_perp  (A4 "Liq-Imbalance Reversion", mean-rev) — FADE the flush:
      a big LONG-liquidation cascade (down flush) in an uptrend = buy the
      forced-selling dip; mirror for shorts. The user's #1 pick.
  liq_zscore_perp     (cont1 "Cascade Z-score", continuation) — FOLLOW the flush
      when liq volume z-scores >= threshold: the cascade has momentum.
  liq_relspike_perp   (cont5 "Relative Liq-Spike", continuation) — FOLLOW when the
      flush is large RELATIVE to the recent regime (rel_spike >= threshold).

Honest: these need the liquidation tape (klines lack it). Live they use the real
Binance forceOrder feed; the backtest uses a price/volume PROXY, so backtest
numbers reflect the LOGIC, not the exact liq edge. They stay shadow-only until
their live track proves out.
"""
from typing import Optional
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from data.tokens import resolve
from data.cascade import detect_cascade
from config import settings


def _perp_symbols() -> set:
    raw = getattr(settings, "perp_symbols", "") or ""
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


def _mk(name, symbol, direction, leverage, conf, sl, tp, rationale) -> Signal:
    token = resolve(symbol)
    return Signal(symbol=symbol, contract=token.contract if token else "",
                  side="buy" if direction == "long" else "sell", confidence=conf,
                  stop_loss_pct=sl, take_profit_pct=tp, max_hold_min=480,
                  rationale=rationale, strategy_name=name,
                  direction=direction, venue="perp", leverage=leverage)


class LiqReversionPerpStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("liq_reversion_perp")

    async def evaluate(self, symbol, candles_5m, candles_1h, market_ctx: MarketContext) -> Optional[Signal]:
        if symbol.upper() not in _perp_symbols():
            return None
        c = detect_cascade(symbol, candles_1h)
        if not c or c["z"] < float(getattr(settings, "liq_z_threshold", 2.5)):
            return None
        lev = float(getattr(settings, "perp_leverage", 3.0))
        regime = market_ctx.regime
        # FADE: down flush -> long (buy the forced dip); up flush -> short
        if c["flush_dir"] == "down":
            if regime not in ("TREND_UP", "CHOP"):
                return None
            direction = "long"
        else:
            if regime not in ("TREND_DOWN", "RISK_OFF", "CHOP"):
                return None
            direction = "short"
        return _mk(self.name, symbol, direction, lev, 0.74, 3.0, 5.0,
                   f"Liq-imbalance REVERSION: fading a {c['flush_dir']} flush (z={c['z']:.1f}, src={c['source']}).")


class LiqZscorePerpStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("liq_zscore_perp")

    async def evaluate(self, symbol, candles_5m, candles_1h, market_ctx: MarketContext) -> Optional[Signal]:
        if symbol.upper() not in _perp_symbols():
            return None
        c = detect_cascade(symbol, candles_1h)
        if not c or c["z"] < float(getattr(settings, "liq_z_threshold", 2.5)):
            return None
        lev = float(getattr(settings, "perp_leverage", 3.0))
        regime = market_ctx.regime
        # FOLLOW: down flush -> short; up flush -> long
        if c["flush_dir"] == "down":
            if regime not in ("TREND_DOWN", "RISK_OFF", "CHOP"):
                return None
            direction = "short"
        else:
            if regime not in ("TREND_UP", "CHOP"):
                return None
            direction = "long"
        return _mk(self.name, symbol, direction, lev, 0.74, 3.0, 6.0,
                   f"Cascade Z-SCORE continuation: following a {c['flush_dir']} flush (z={c['z']:.1f}, src={c['source']}).")


class LiqRelspikePerpStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("liq_relspike_perp")

    async def evaluate(self, symbol, candles_5m, candles_1h, market_ctx: MarketContext) -> Optional[Signal]:
        if symbol.upper() not in _perp_symbols():
            return None
        c = detect_cascade(symbol, candles_1h)
        if not c or c["rel_spike"] < float(getattr(settings, "liq_relspike_threshold", 3.0)):
            return None
        lev = float(getattr(settings, "perp_leverage", 3.0))
        regime = market_ctx.regime
        # FOLLOW the relative spike
        if c["flush_dir"] == "down":
            if regime not in ("TREND_DOWN", "RISK_OFF", "CHOP"):
                return None
            direction = "short"
        else:
            if regime not in ("TREND_UP", "CHOP"):
                return None
            direction = "long"
        return _mk(self.name, symbol, direction, lev, 0.72, 3.0, 6.0,
                   f"Relative liq-SPIKE continuation: {c['rel_spike']:.1f}x {c['flush_dir']} flush (src={c['source']}).")
