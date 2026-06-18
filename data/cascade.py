"""Liquidation-cascade detection shared by the liq-flow strategies.

Tries the REAL liquidation feed first (data/liq_feed) for live trading; falls back
to a kline-derived PROXY (a large z-scored 1h move on a volume spike ~ forced
flow) so the same strategies are backtestable. Returns a neutral descriptor —
each strategy decides whether to FADE (reversion) or FOLLOW (continuation) the
flush, and applies its own z / relative-spike threshold.
"""
from typing import Optional, Dict, Any, List


def _kline_proxy(candles_1h) -> Optional[Dict[str, Any]]:
    if len(candles_1h) < 55:
        return None
    closes = [c.close for c in candles_1h]
    vols = [c.volume for c in candles_1h]
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 50:
        return None
    recent = rets[-50:]
    mean = sum(recent) / len(recent)
    std = (sum((x - mean) ** 2 for x in recent) / len(recent)) ** 0.5
    r = rets[-1]
    z = (r - mean) / std if std > 0 else 0.0
    avg_vol = sum(vols[-21:-1]) / 20.0
    rel = vols[-1] / avg_vol if avg_vol > 0 else 0.0
    return {"flush_dir": "down" if r < 0 else "up", "z": abs(z), "rel_spike": rel, "source": "proxy"}


def detect_cascade(symbol: str, candles_1h: List) -> Optional[Dict[str, Any]]:
    """Returns {flush_dir, z, rel_spike, source} or None. flush_dir is the way the
    cascade pushed price ('down' = longs liquidated, 'up' = shorts liquidated)."""
    try:
        from data import liq_feed
        m = liq_feed.liq_metrics(symbol)
        if m:
            return {"flush_dir": m["flush_dir"], "z": abs(m["zscore"]),
                    "rel_spike": m["rel_spike"], "source": "liq"}
    except Exception:
        pass
    return _kline_proxy(candles_1h)
