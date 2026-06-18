"""10 liquidation + trend-break ideas (long/short perp).

Each combines cascade detection (real liq feed live, kline proxy for backtest)
with a trend-break / market-structure condition. Regime-disciplined. All start
DISABLED — backtested first; only the winners get enabled, the rest shadow-tested.
"""
from typing import Optional, List
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from data.tokens import resolve
from data.cascade import detect_cascade
from strategies.macd_perp import _ema_series
from strategies.trend_follow import calculate_atr
from strategies.rsi_div_perp import rsi_series
from config import settings


def _ps() -> set:
    raw = getattr(settings, "perp_symbols", "") or ""
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


def _ema(closes: List[float], n: int) -> float:
    return _ema_series(closes, n)[-1] if closes else 0.0


def _lev() -> float:
    return float(getattr(settings, "perp_leverage", 3.0))


def _zt() -> float:
    return float(getattr(settings, "liq_z_threshold", 2.5))


def _mk(name, symbol, direction, conf, sl, tp, rationale, mh=480) -> Signal:
    t = resolve(symbol)
    return Signal(symbol=symbol, contract=t.contract if t else "",
                  side="buy" if direction == "long" else "sell", confidence=conf,
                  stop_loss_pct=sl, take_profit_pct=tp, max_hold_min=mh,
                  rationale=rationale, strategy_name=name,
                  direction=direction, venue="perp", leverage=_lev())


def _pre(symbol, c1h, min_bars=55):
    return symbol.upper() in _ps() and len(c1h) >= min_bars


# 1 — Liq flush INTO support -> bounce (reversion)
class LiqSupportReversionPerp(BaseStrategy):
    def __init__(self): super().__init__("liq_support_reversion_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if not _pre(symbol, c1h): return None
        c = detect_cascade(symbol, c1h)
        if not c or c["z"] < _zt(): return None
        lows = [x.low for x in c1h]; highs = [x.high for x in c1h]; cur = c1h[-1].close
        if c["flush_dir"] == "down" and ctx.regime in ("TREND_UP", "CHOP"):
            sup = min(lows[-50:-1])
            if cur <= sup * 1.015:
                return _mk(self.name, symbol, "long", 0.76, 3.0, 5.0,
                           f"Liq flush into support ${sup:.4f} (z={c['z']:.1f}) -> bounce long.")
        if c["flush_dir"] == "up" and ctx.regime in ("TREND_DOWN", "RISK_OFF", "CHOP"):
            res = max(highs[-50:-1])
            if cur >= res * 0.985:
                return _mk(self.name, symbol, "short", 0.76, 3.0, 5.0,
                           f"Liq squeeze into resistance ${res:.4f} (z={c['z']:.1f}) -> short.")
        return None


# 2 — Trend break thru 50-EMA + liq confirm (continuation)
class LiqTrendBreakPerp(BaseStrategy):
    def __init__(self): super().__init__("liq_trendbreak_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if not _pre(symbol, c1h): return None
        c = detect_cascade(symbol, c1h)
        if not c or c["z"] < _zt(): return None
        closes = [x.close for x in c1h]; ema50 = _ema(closes, 50); cur, prev = closes[-1], closes[-2]
        if c["flush_dir"] == "down" and prev >= ema50 and cur < ema50 and ctx.regime in ("TREND_DOWN", "RISK_OFF", "CHOP"):
            return _mk(self.name, symbol, "short", 0.74, 3.0, 6.0, "Break below 50-EMA + long-liq flush -> short.")
        if c["flush_dir"] == "up" and prev <= ema50 and cur > ema50 and ctx.regime in ("TREND_UP", "CHOP"):
            return _mk(self.name, symbol, "long", 0.74, 3.0, 6.0, "Break above 50-EMA + short-liq squeeze -> long.")
        return None


# 3 — Failed breakdown / liq sweep + reclaim (trap)
class LiqFailedBreakdownPerp(BaseStrategy):
    def __init__(self): super().__init__("liq_failed_breakdown_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if not _pre(symbol, c1h): return None
        c = detect_cascade(symbol, c1h)
        if not c or c["z"] < _zt(): return None
        lows = [x.low for x in c1h]; highs = [x.high for x in c1h]; bar = c1h[-1]
        if c["flush_dir"] == "down" and ctx.regime in ("TREND_UP", "CHOP"):
            rng_low = min(lows[-21:-1])
            if bar.low < rng_low and bar.close > rng_low:
                return _mk(self.name, symbol, "long", 0.78, 3.0, 5.0, "Failed breakdown: liq swept range low then reclaimed -> long.")
        if c["flush_dir"] == "up" and ctx.regime in ("TREND_DOWN", "RISK_OFF", "CHOP"):
            rng_high = max(highs[-21:-1])
            if bar.high > rng_high and bar.close < rng_high:
                return _mk(self.name, symbol, "short", 0.78, 3.0, 5.0, "Failed breakout: liq swept range high then rejected -> short.")
        return None


# 4 — EMA-ribbon flip (8/21/55) + liq
class LiqRibbonFlipPerp(BaseStrategy):
    def __init__(self): super().__init__("liq_ribbon_flip_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if not _pre(symbol, c1h): return None
        c = detect_cascade(symbol, c1h)
        if not c or c["z"] < _zt(): return None
        closes = [x.close for x in c1h]; e8, e21, e55 = _ema(closes, 8), _ema(closes, 21), _ema(closes, 55)
        if e8 < e21 < e55 and c["flush_dir"] == "down" and ctx.regime in ("TREND_DOWN", "RISK_OFF", "CHOP"):
            return _mk(self.name, symbol, "short", 0.74, 3.0, 6.0, "Bearish EMA-ribbon + long-liq flush -> short.")
        if e8 > e21 > e55 and c["flush_dir"] == "up" and ctx.regime in ("TREND_UP", "CHOP"):
            return _mk(self.name, symbol, "long", 0.74, 3.0, 6.0, "Bullish EMA-ribbon + short-liq squeeze -> long.")
        return None


# 5 — Liq volume climax at lows -> capitulation (reversion)
class LiqClimaxReversionPerp(BaseStrategy):
    def __init__(self): super().__init__("liq_climax_reversion_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if not _pre(symbol, c1h): return None
        c = detect_cascade(symbol, c1h)
        if not c or c["z"] < 3.0: return None
        vols = [x.volume for x in c1h]; lows = [x.low for x in c1h]; highs = [x.high for x in c1h]; cur = c1h[-1].close
        avg = sum(vols[-21:-1]) / 20.0
        if avg <= 0 or vols[-1] < 3.0 * avg: return None
        if c["flush_dir"] == "down" and ctx.regime in ("TREND_UP", "CHOP"):
            lo = min(lows[-100:-1]) if len(lows) >= 100 else min(lows[:-1])
            if cur <= lo * 1.02:
                return _mk(self.name, symbol, "long", 0.74, 3.5, 6.0, "Liq volume climax at lows -> capitulation long.")
        if c["flush_dir"] == "up" and ctx.regime in ("TREND_DOWN", "RISK_OFF", "CHOP"):
            hi = max(highs[-100:-1]) if len(highs) >= 100 else max(highs[:-1])
            if cur >= hi * 0.98:
                return _mk(self.name, symbol, "short", 0.74, 3.5, 6.0, "Liq volume climax at highs -> blow-off short.")
        return None


# 6 — Market-structure break (BOS) + liq (continuation)
class LiqStructureBreakPerp(BaseStrategy):
    def __init__(self): super().__init__("liq_structure_break_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if not _pre(symbol, c1h): return None
        c = detect_cascade(symbol, c1h)
        if not c or c["z"] < _zt(): return None
        closes = [x.close for x in c1h]; highs = [x.high for x in c1h]; lows = [x.low for x in c1h]; cur = closes[-1]
        swing_low = min(lows[-12:-2]); swing_high = max(highs[-12:-2])
        if cur < swing_low and c["flush_dir"] == "down" and ctx.regime in ("TREND_DOWN", "RISK_OFF", "CHOP"):
            return _mk(self.name, symbol, "short", 0.74, 3.0, 6.0, "Bearish structure break + liq flush -> short.")
        if cur > swing_high and c["flush_dir"] == "up" and ctx.regime in ("TREND_UP", "CHOP"):
            return _mk(self.name, symbol, "long", 0.74, 3.0, 6.0, "Bullish structure break + liq squeeze -> long.")
        return None


# 7 — Liq flush wick rejection (reversal)
class LiqWickRejectionPerp(BaseStrategy):
    def __init__(self): super().__init__("liq_wick_rejection_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if not _pre(symbol, c1h): return None
        c = detect_cascade(symbol, c1h)
        if not c or c["z"] < _zt(): return None
        bar = c1h[-1]; body = abs(bar.close - bar.open); rng = bar.high - bar.low
        if rng <= 0: return None
        low_wick = min(bar.open, bar.close) - bar.low; up_wick = bar.high - max(bar.open, bar.close)
        if c["flush_dir"] == "down" and low_wick >= 2 * body and low_wick >= 0.5 * rng and ctx.regime in ("TREND_UP", "CHOP"):
            return _mk(self.name, symbol, "long", 0.76, 3.0, 5.0, "Liq flush rejected (long lower wick) -> long.")
        if c["flush_dir"] == "up" and up_wick >= 2 * body and up_wick >= 0.5 * rng and ctx.regime in ("TREND_DOWN", "RISK_OFF", "CHOP"):
            return _mk(self.name, symbol, "short", 0.76, 3.0, 5.0, "Liq squeeze rejected (long upper wick) -> short.")
        return None


# 8 — Donchian break + liq fuel (continuation)
class LiqDonchianAccelPerp(BaseStrategy):
    def __init__(self): super().__init__("liq_donchian_accel_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if not _pre(symbol, c1h): return None
        c = detect_cascade(symbol, c1h)
        if not c or c["z"] < _zt(): return None
        closes = [x.close for x in c1h]; highs = [x.high for x in c1h]; lows = [x.low for x in c1h]; cur = closes[-1]
        chan_hi = max(highs[-21:-1]); chan_lo = min(lows[-21:-1])
        if cur > chan_hi and c["flush_dir"] == "up" and ctx.regime in ("TREND_UP", "CHOP"):
            return _mk(self.name, symbol, "long", 0.76, 3.0, 6.0, "Donchian break + short-liq fuel -> long.")
        if cur < chan_lo and c["flush_dir"] == "down" and ctx.regime in ("TREND_DOWN", "RISK_OFF", "CHOP"):
            return _mk(self.name, symbol, "short", 0.76, 3.0, 6.0, "Donchian breakdown + long-liq fuel -> short.")
        return None


# 9 — Divergence at liq squeeze high/low -> fade (reversal)
class LiqDivergenceFadePerp(BaseStrategy):
    def __init__(self): super().__init__("liq_divergence_fade_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if not _pre(symbol, c1h): return None
        c = detect_cascade(symbol, c1h)
        if not c: return None
        closes = [x.close for x in c1h]; highs = [x.high for x in c1h]; lows = [x.low for x in c1h]
        rsi = rsi_series(closes, 14)
        if c["flush_dir"] == "up" and ctx.regime in ("TREND_DOWN", "RISK_OFF", "CHOP"):
            if highs[-1] > max(highs[-25:-3]) and rsi[-1] < max(rsi[-25:-3]) and rsi[-1] > 60:
                return _mk(self.name, symbol, "short", 0.74, 3.0, 5.0, "Bearish RSI divergence at liq-squeeze high -> short.")
        if c["flush_dir"] == "down" and ctx.regime in ("TREND_UP", "CHOP"):
            if lows[-1] < min(lows[-25:-3]) and rsi[-1] > min(rsi[-25:-3]) and rsi[-1] < 40:
                return _mk(self.name, symbol, "long", 0.74, 3.0, 5.0, "Bullish RSI divergence at liq-flush low -> long.")
        return None


# 10 — Vol-squeeze break + liq (continuation)
class LiqSqueezeBreakPerp(BaseStrategy):
    def __init__(self): super().__init__("liq_squeeze_break_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if not _pre(symbol, c1h): return None
        c = detect_cascade(symbol, c1h)
        if not c or c["z"] < _zt(): return None
        closes = [x.close for x in c1h]; win = closes[-20:]
        basis = sum(win) / 20.0
        std = (sum((x - basis) ** 2 for x in win) / 20.0) ** 0.5
        atr = calculate_atr(c1h[-21:], 20); ema = _ema(closes, 20)
        # squeeze = BB(20,2) inside KC(20,1.5)
        squeeze = (basis + 2 * std) < (ema + 1.5 * atr) and (basis - 2 * std) > (ema - 1.5 * atr)
        if not squeeze:
            return None
        cur = closes[-1]
        if c["flush_dir"] == "up" and cur > basis and ctx.regime in ("TREND_UP", "CHOP"):
            return _mk(self.name, symbol, "long", 0.74, 3.0, 6.0, "Vol-squeeze + up-liq break -> long.")
        if c["flush_dir"] == "down" and cur < basis and ctx.regime in ("TREND_DOWN", "RISK_OFF", "CHOP"):
            return _mk(self.name, symbol, "short", 0.74, 3.0, 6.0, "Vol-squeeze + down-liq break -> short.")
        return None


IDEAS = {
    "liq_support_reversion_perp": LiqSupportReversionPerp,
    "liq_trendbreak_perp": LiqTrendBreakPerp,
    "liq_failed_breakdown_perp": LiqFailedBreakdownPerp,
    "liq_ribbon_flip_perp": LiqRibbonFlipPerp,
    "liq_climax_reversion_perp": LiqClimaxReversionPerp,
    "liq_structure_break_perp": LiqStructureBreakPerp,
    "liq_wick_rejection_perp": LiqWickRejectionPerp,
    "liq_donchian_accel_perp": LiqDonchianAccelPerp,
    "liq_divergence_fade_perp": LiqDivergenceFadePerp,
    "liq_squeeze_break_perp": LiqSqueezeBreakPerp,
}
