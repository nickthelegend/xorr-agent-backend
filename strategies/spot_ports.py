"""Spot-viable strategies ported from trader.dev's broader leaderboard (not just the
top-3 oscillators). We surveyed ~283 strategies; most of the top-Sharpe ones were on
gold/forex (XAUUSD/PAXG) or were genetically-evolved overfit parameter soup. The
genuinely NEW, crypto, long-spot-viable archetypes we didn't already have:

  stochrsi_mr_perp  — StochRSI Mean Reversion (RSI 14 -> Stochastic 14). trader.dev:
                      "StochRSI MR" on ADA/ETH/NEAR/LINK 4h, win 65-80%. NEW archetype
                      (we had RSI and plain Stochastic, not Stochastic-of-RSI).
  adx_trend_perp    — ADX/DMI trend (+DI/-DI cross, ADX>thresh, EMA200 regime). NEW
                      archetype (we had no ADX). Trend-following -> expect weaker OOS.
  bb_breakout_perp  — Bollinger breakout (close crosses band) + EMA50 filter. trader.dev
                      "BB Breakout Opt". Long-breakout -> momentum, expect weaker OOS.

All are symmetric (long+short) + regime-gated, so they work in BOTH venues: spot takes
the LONG side only (via the spot-only pipeline), perps takes both. Long side = the
spot-implementable half. Gated through OUR --spot backtest before enabling (their Pine
metrics are in-sample and often on instruments we can't trade).
"""
from typing import List, Optional
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from strategies.rsi_div_perp import rsi_series
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


def _dir_ok(direction, regime):
    if direction == "long":
        return regime in ("TREND_UP", "CHOP")
    return regime in ("TREND_DOWN", "RISK_OFF", "CHOP")


# --- StochRSI (Stochastic applied to the RSI series), 0..1 ---
def stochrsi_series(closes: List[float], rsi_len: int = 14, stoch_len: int = 14) -> List[float]:
    r = rsi_series(closes, rsi_len)
    if len(r) < stoch_len + 2:
        return []
    out = []
    for i in range(stoch_len - 1, len(r)):
        win = r[i - stoch_len + 1:i + 1]
        lo, hi = min(win), max(win)
        out.append((r[i] - lo) / (hi - lo) if hi > lo else 0.5)
    return out


# --- Wilder RMA + ADX/DMI ---
def _rma(vals: List[float], n: int) -> List[float]:
    if len(vals) < n:
        return []
    out = [sum(vals[:n]) / n]
    for v in vals[n:]:
        out.append((out[-1] * (n - 1) + v) / n)
    return out  # aligned to vals[n-1:]


def adx_dmi(candles, n: int = 14) -> Optional[dict]:
    """Returns last-two +DI/-DI and latest ADX (Wilder), or None if too short."""
    if len(candles) < 2 * n + 2:
        return None
    tr, pdm, mdm = [], [], []
    for i in range(1, len(candles)):
        h, l, pc = candles[i].high, candles[i].low, candles[i - 1].close
        ph, pl = candles[i - 1].high, candles[i - 1].low
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
        up, dn = h - ph, pl - l
        pdm.append(up if (up > dn and up > 0) else 0.0)
        mdm.append(dn if (dn > up and dn > 0) else 0.0)
    str_, spdm, smdm = _rma(tr, n), _rma(pdm, n), _rma(mdm, n)
    if len(str_) < n + 2:
        return None
    pdi = [100.0 * spdm[i] / str_[i] if str_[i] > 0 else 0.0 for i in range(len(str_))]
    mdi = [100.0 * smdm[i] / str_[i] if str_[i] > 0 else 0.0 for i in range(len(str_))]
    dx = [100.0 * abs(pdi[i] - mdi[i]) / (pdi[i] + mdi[i]) if (pdi[i] + mdi[i]) > 0 else 0.0
          for i in range(len(pdi))]
    adx = _rma(dx, n)
    if len(adx) < 1:
        return None
    return {"pdi_prev": pdi[-2], "pdi": pdi[-1], "mdi_prev": mdi[-2], "mdi": mdi[-1], "adx": adx[-1]}


def _sma(v, n):
    return sum(v[-n:]) / n


def _stdev(v, n):
    m = _sma(v, n)
    return (sum((x - m) ** 2 for x in v[-n:]) / n) ** 0.5


class StochRsiMrPerp(BaseStrategy):
    """trader.dev StochRSI MR: long when StochRSI crosses under oversold (fade), short
    when it crosses over overbought. Mean-reversion — the LONG side is the spot edge."""
    def __init__(self): super().__init__("stochrsi_mr_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 40:
            return None
        s = stochrsi_series([x.close for x in c1h],
                            int(getattr(settings, "stochrsi_rsi_len", 14)),
                            int(getattr(settings, "stochrsi_stoch_len", 14)))
        if len(s) < 2:
            return None
        os_t = float(getattr(settings, "stochrsi_oversold", 0.2))
        ob_t = float(getattr(settings, "stochrsi_overbought", 0.8))
        prev, cur = s[-2], s[-1]
        if prev >= os_t and cur < os_t and _dir_ok("long", ctx.regime):
            return _mk(self.name, symbol, "long", 0.76, 3.5, 5.0,
                       f"StochRSI crossunder {os_t:.2f} ({cur:.2f}) -> MR long.")
        if prev <= ob_t and cur > ob_t and _dir_ok("short", ctx.regime):
            return _mk(self.name, symbol, "short", 0.76, 3.5, 5.0,
                       f"StochRSI crossover {ob_t:.2f} ({cur:.2f}) -> MR short.")
        return None


class AdxTrendPerp(BaseStrategy):
    """Clean ADX/DMI trend: long when +DI crosses above -DI with ADX>thresh and price
    above EMA200 (confirmed uptrend); mirror for short. Trend-following."""
    def __init__(self): super().__init__("adx_trend_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 60:
            return None
        a = adx_dmi(c1h, int(getattr(settings, "adx_len", 14)))
        if not a:
            return None
        thr = float(getattr(settings, "adx_threshold", 25.0))
        closes = [x.close for x in c1h]
        ema200 = _ema_series(closes, 200)[-1] if len(closes) >= 200 else _ema_series(closes, len(closes))[-1]
        cur = closes[-1]
        cross_up = a["pdi_prev"] <= a["mdi_prev"] and a["pdi"] > a["mdi"]
        cross_dn = a["mdi_prev"] <= a["pdi_prev"] and a["mdi"] > a["pdi"]
        if cross_up and a["adx"] >= thr and cur > ema200 and _dir_ok("long", ctx.regime):
            return _mk(self.name, symbol, "long", 0.72, 3.0, 6.0,
                       f"+DI>-DI, ADX {a['adx']:.0f}>{thr:.0f}, >EMA200 -> trend long.")
        if cross_dn and a["adx"] >= thr and cur < ema200 and _dir_ok("short", ctx.regime):
            return _mk(self.name, symbol, "short", 0.72, 3.0, 6.0,
                       f"-DI>+DI, ADX {a['adx']:.0f}>{thr:.0f}, <EMA200 -> trend short.")
        return None


class BbBreakoutPerp(BaseStrategy):
    """trader.dev BB Breakout: long when close crosses above the upper Bollinger band
    while above the EMA filter; mirror for short. Breakout/momentum."""
    def __init__(self): super().__init__("bb_breakout_perp")
    async def evaluate(self, symbol, c5, c1h, ctx):
        if symbol.upper() not in _ps() or len(c1h) < 60:
            return None
        closes = [x.close for x in c1h]
        n = int(getattr(settings, "bb_break_len", 10))
        mult = float(getattr(settings, "bb_break_mult", 1.5))
        ema_f = _ema_series(closes, int(getattr(settings, "bb_break_ema", 50)))[-1]
        b_prev, d_prev = _sma(closes[:-1], n), _stdev(closes[:-1], n)
        b_cur, d_cur = _sma(closes, n), _stdev(closes, n)
        up_prev, up_cur = b_prev + mult * d_prev, b_cur + mult * d_cur
        lo_prev, lo_cur = b_prev - mult * d_prev, b_cur - mult * d_cur
        prev_c, cur_c = closes[-2], closes[-1]
        if prev_c <= up_prev and cur_c > up_cur and cur_c > ema_f and _dir_ok("long", ctx.regime):
            return _mk(self.name, symbol, "long", 0.72, 3.0, 5.0,
                       "Close broke above upper BB + >EMA -> breakout long.")
        if prev_c >= lo_prev and cur_c < lo_cur and cur_c < ema_f and _dir_ok("short", ctx.regime):
            return _mk(self.name, symbol, "short", 0.72, 3.0, 5.0,
                       "Close broke below lower BB + <EMA -> breakdown short.")
        return None


IDEAS = {
    "stochrsi_mr_perp": StochRsiMrPerp,
    "adx_trend_perp": AdxTrendPerp,
    "bb_breakout_perp": BbBreakoutPerp,
}
