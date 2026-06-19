"""Confluence engine — "verify a setup is real by checking it against all the strats."

The perp-derived strategy classes gate on `symbol in perp_symbols` (only ~7 coins), so we
can't just instantiate them against an arbitrary watchlist coin. Instead we run the SAME
indicator math those strategies use — RSI, StochRSI, Williams %R, CCI, MFI, Bollinger,
EMA-stretch, range position, Donchian — directly against the coin's 1h candles, UNGATED,
and count how many INDEPENDENT lenses agree there's a long here right now.

1 lens firing = noise. 3+ agreeing = a real, multi-confirmed setup. This count is handed to
Claude as the "is it real?" evidence and lets the deterministic fallback prefer
high-confluence names. Pure math, no LLM, cheap.
"""
import asyncio
from typing import List

from core.types import Candle
from strategies.spot_ports import stochrsi_series
from strategies.reversion_oscillators import cci_last2, williams_last2, mfi_last2, _bb
from strategies.rsi_div_perp import rsi_series

# 8 reversion (oversold-long) lenses + 2 breakout (upside-long) lenses = the panel.
_TOTAL = 10
_MAX_AGREE = 4.0   # this many agreeing lenses == full confluence (score 1.0)


def _ema(vals: List[float], n: int) -> float:
    if not vals:
        return 0.0
    k = 2.0 / (n + 1.0)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1.0 - k)
    return e


def confluence_panel(symbol: str, c1h: List[Candle]) -> dict:
    """Run every indicator lens against one coin's 1h candles → agreement summary."""
    closes = [c.close for c in c1h]
    sym = symbol.upper()
    if len(closes) < 30:
        return {"symbol": sym, "agree": 0, "rev_agree": 0, "brk_agree": 0,
                "total": _TOTAL, "score": 0.0, "side": "none", "firing": []}
    price = closes[-1]
    rev: List[str] = []   # reversion lenses that fired (oversold → long)
    brk: List[str] = []   # breakout lenses that fired (upside → long)

    # --- reversion lenses (the validated edge: fade oversold flushes) ---
    r = rsi_series(closes, 14)
    if r and r[-1] < 35.0:
        rev.append(f"rsi{r[-1]:.0f}")
    s = stochrsi_series(closes, 14, 14)
    if s and s[-1] < 0.20:
        rev.append(f"stochrsi{s[-1]:.2f}")
    w = williams_last2(c1h, 14)
    if w and w[1] < -80.0:
        rev.append(f"williams{w[1]:.0f}")
    cc = cci_last2(c1h, 20)
    if cc and cc[1] < -100.0:
        rev.append(f"cci{cc[1]:.0f}")
    mf = mfi_last2(c1h, 14)
    if mf and mf[1] < 20.0:
        rev.append(f"mfi{mf[1]:.0f}")
    basis, upper, lower = _bb(closes, 20, 2.0)
    if price < lower:
        rev.append("bb_lower")
    ema20 = _ema(closes[-60:], 20)
    if ema20 > 0 and (price - ema20) / ema20 * 100.0 < -3.0:
        rev.append("ema_stretch")
    seg = c1h[-20:]
    hi = max(c.high for c in seg)
    lo = min(c.low for c in seg)
    rng_pos = (price - lo) / (hi - lo) if hi > lo else 0.5
    if rng_pos < 0.20:
        rev.append("range_low")

    # --- breakout lenses (momentum: weaker OOS, kept honest + few) ---
    if price > upper:
        brk.append("bb_upper")
    dhi = max(c.high for c in c1h[-21:-1]) if len(c1h) >= 21 else hi
    if price >= dhi:
        brk.append("donchian")

    rev_agree, brk_agree = len(rev), len(brk)
    if rev_agree >= brk_agree:
        side, agree, firing = "reversion", rev_agree, rev
    else:
        side, agree, firing = "breakout", brk_agree, brk
    score = round(min(1.0, agree / _MAX_AGREE), 2)
    return {"symbol": sym, "agree": agree, "rev_agree": rev_agree, "brk_agree": brk_agree,
            "total": _TOTAL, "score": score, "side": side, "firing": firing,
            "range_pos": round(rng_pos, 2)}


async def attach_confluence(ranked: List[dict], top_n: int = 10) -> None:
    """Compute the confluence panel for the top-N ranked coins; attach in place as
    `r['confluence']`. Fetches 1h klines concurrently (bounded). Fail-open per coin."""
    from data.binance_klines import fetch_binance_klines
    sem = asyncio.Semaphore(8)

    async def _one(r: dict):
        sym = str(r.get("symbol", "")).upper()
        if not sym:
            return
        async with sem:
            try:
                c1h = await fetch_binance_klines(sym, "1h", limit=120)
            except Exception:
                c1h = []
        if c1h:
            r["confluence"] = confluence_panel(sym, c1h)

    await asyncio.gather(*[_one(r) for r in ranked[:top_n]])
