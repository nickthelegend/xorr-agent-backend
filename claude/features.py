"""Per-coin technical features for the Claude watchlist agent.

Computed from 1h klines, self-contained (no strategy imports) so the watchlist layer
is independent. Each feature is something Claude can reason about when picking what to
play and which strategy fits:

  vol_spike      last bar volume / 20-bar average        (climax / participation)
  atr_pct        ATR(14) / price                          (how volatile)
  atr_expansion  recent ATR / older-baseline ATR          (volatility GAP — breakout fuel)
  ret_24h        % change over last 24 bars               (momentum)
  ret_4h         % change over last 4 bars                (near-term thrust/flush)
  rsi            RSI(14)                                   (oversold/overbought)
  ema_dist_pct   (price - EMA20) / EMA20 * 100            (stretch from mean)
  range_pos      position in the recent 48-bar range 0..1 (0 = at lows, 1 = at highs)

Plus two derived archetype scores so Claude (and the deterministic fallback) can see
at a glance which setup each coin favors:

  reversion_score  high when oversold + flushed down + volume climax near range lows
  breakout_score   high when thrusting up + volatility expanding + near range highs
"""
from typing import List, Optional
from core.types import Candle


def _sma(v: List[float], n: int) -> float:
    if not v:
        return 0.0
    n = min(n, len(v))
    return sum(v[-n:]) / n


def _ema_last(v: List[float], n: int) -> float:
    if not v:
        return 0.0
    if len(v) < n:
        return v[-1]
    k = 2.0 / (n + 1)
    e = sum(v[:n]) / n
    for x in v[n:]:
        e = (x - e) * k + e
    return e


def _rsi(closes: List[float], n: int = 14) -> float:
    if len(closes) < n + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(len(closes) - n, len(closes)):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / n, losses / n
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


def _atr(candles: List[Candle], n: int = 14) -> float:
    if len(candles) < n + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i].high, candles[i].low, candles[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:n]) / n
    for t in trs[n:]:
        a = (a * (n - 1) + t) / n
    return a


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def compute_features(symbol: str, c1h: List[Candle]) -> Optional[dict]:
    """Returns the feature dict for one coin, or None if too little data."""
    if len(c1h) < 60:
        return None
    closes = [x.close for x in c1h]
    vols = [x.volume for x in c1h]
    highs = [x.high for x in c1h]
    lows = [x.low for x in c1h]
    price = closes[-1]
    if price <= 0:
        return None

    avg_vol = _sma(vols[:-1], 20)
    vol_spike = (vols[-1] / avg_vol) if avg_vol > 0 else 1.0

    atr_now = _atr(c1h[-30:], 14)
    atr_base = _atr(c1h[-80:-20], 14) if len(c1h) >= 80 else atr_now
    atr_pct = (atr_now / price) * 100.0
    atr_expansion = (atr_now / atr_base) if atr_base > 0 else 1.0

    ret_24h = ((price / closes[-25]) - 1.0) * 100.0 if len(closes) >= 25 else 0.0
    ret_4h = ((price / closes[-5]) - 1.0) * 100.0 if len(closes) >= 5 else 0.0
    rsi = _rsi(closes, 14)
    ema20 = _ema_last(closes, 20)
    ema_dist_pct = ((price - ema20) / ema20) * 100.0 if ema20 > 0 else 0.0

    win = 48
    hi = max(highs[-win:])
    lo = min(lows[-win:])
    range_pos = _clamp((price - lo) / (hi - lo)) if hi > lo else 0.5

    # --- archetype scores (0..1) ---
    # reversion: oversold RSI + down thrust + volume climax + near range lows
    reversion_score = _clamp(
        0.35 * _clamp((45.0 - rsi) / 25.0)          # RSI below ~45, stronger as it drops
        + 0.25 * _clamp(-ret_4h / 6.0)               # recent down flush
        + 0.20 * _clamp((vol_spike - 1.3) / 1.7)     # volume climax
        + 0.20 * _clamp((0.35 - range_pos) / 0.35)   # near the lows
    )
    # breakout: up thrust + volatility expanding + volume + near range highs
    breakout_score = _clamp(
        0.30 * _clamp(ret_4h / 6.0)
        + 0.25 * _clamp((atr_expansion - 1.0) / 0.8)
        + 0.20 * _clamp((vol_spike - 1.3) / 1.7)
        + 0.25 * _clamp((range_pos - 0.7) / 0.3)
    )
    opportunity = max(reversion_score, breakout_score)

    return {
        "symbol": symbol,
        "price": round(price, 6),
        "vol_spike": round(vol_spike, 2),
        "atr_pct": round(atr_pct, 2),
        "atr_expansion": round(atr_expansion, 2),
        "ret_24h": round(ret_24h, 2),
        "ret_4h": round(ret_4h, 2),
        "rsi": round(rsi, 1),
        "ema_dist_pct": round(ema_dist_pct, 2),
        "range_pos": round(range_pos, 2),
        "reversion_score": round(reversion_score, 3),
        "breakout_score": round(breakout_score, 3),
        "opportunity": round(opportunity, 3),
    }
