"""Watchlist agent — scans the eligible-coin universe (~70 BEP-20 majors) every few
hours, scores each coin on technical features, and produces a ranked watchlist.

Runs the scan concurrently (bounded) so ~70 kline fetches finish quickly, then ranks
by opportunity score. The ranked top-N + the market regime are handed to Claude
(claude/claude_brain.py), which picks what to play and which enabled strategy fits.

CLI:  python -m claude.watchlist_agent            # scan + score + print the table
      python -m claude.watchlist_agent --decide   # also ask Claude to pick
"""
import argparse
import asyncio
from typing import List

from config import settings
from data.tokens import iter_tradable
from data.binance_klines import fetch_binance_klines
from filters.regime import classify_market_regime
from claude.features import compute_features


def _universe(limit: int) -> List[str]:
    """Eligible + liquid tradable symbols (the things we can actually buy on spot)."""
    syms = []
    seen = set()
    for t in iter_tradable():
        s = t.symbol.upper()
        if s and s not in seen:
            seen.add(s)
            syms.append(s)
    return syms[:limit]


async def _score_one(symbol: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        try:
            c1h = await fetch_binance_klines(symbol, "1h", limit=120)
        except Exception:
            return None
        if not c1h:
            return None
        return compute_features(symbol, c1h)


async def build_watchlist(top: int = 30) -> dict:
    """Scan the universe, score every coin, return the ranked top-N + metadata."""
    universe = _universe(int(getattr(settings, "watchlist_universe_size", 70)))
    regime = await classify_market_regime()
    sem = asyncio.Semaphore(8)
    results = await asyncio.gather(*[_score_one(s, sem) for s in universe])
    scored = [r for r in results if r]
    scored.sort(key=lambda r: r["opportunity"], reverse=True)
    return {
        "regime": regime,
        "scanned": len(universe),
        "ranked": scored[:top],
    }


def _print_table(wl: dict):
    print(f"regime={wl['regime']}  scanned={wl['scanned']}  showing top {len(wl['ranked'])}")
    print(f"{'sym':8} {'opp':>5} {'rev':>5} {'brk':>5} {'rsi':>5} {'ret4h':>6} {'ret24h':>7} {'volx':>5} {'atrx':>5} {'rngpos':>6}")
    for r in wl["ranked"]:
        print(f"{r['symbol']:8} {r['opportunity']:5.2f} {r['reversion_score']:5.2f} {r['breakout_score']:5.2f} "
              f"{r['rsi']:5.1f} {r['ret_4h']:6.1f} {r['ret_24h']:7.1f} {r['vol_spike']:5.1f} "
              f"{r['atr_expansion']:5.2f} {r['range_pos']:6.2f}")


async def _main(decide: bool):
    wl = await build_watchlist()
    _print_table(wl)
    if decide:
        from claude.claude_brain import decide_playbook
        import json
        pb = await decide_playbook(wl)
        print("\n=== CLAUDE PLAYBOOK ===")
        print(json.dumps(pb, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--decide", action="store_true", help="also ask Claude to pick what to play")
    args = ap.parse_args()
    asyncio.run(_main(args.decide))
