"""Funding-rate skills from the CoinMarketCap skills marketplace, distilled into a
per-symbol funding state and a funding-FADE bias for the perp book.

Skills used (live MCP, cached, fail-open):
  - compare_funding_rate_across_venues  -> pack_crowding_regime, stretch, dispersion
  - detect_funding_rate_regime_shift     -> regime_state, sign_flip, oi-weighted shift

The edge (senpi pangolin/owl archetype): extreme funding marks the CROWDED side.
Crowded longs pay funding and are squeeze-prone -> fade them (favor SHORT, disfavor
new LONG). Crowded shorts -> favor LONG. This biases perp signal confidence.

Live-only (no historical funding series to replay), so it does not affect backtests.
"""
import logging
from typing import Any, Dict, Tuple

from data.cmc_mcp import get_cached_mcp_skill
from data.cmc_signals import _extract_report
from config import settings

logger = logging.getLogger("xorr.data.funding")

# Funding is "extreme" (actionable for a fade) past these thresholds.
EXTREME_FUNDING_BPS = 30.0
EXTREME_STRETCH = 40.0


async def get_funding_state(symbol: str, ttl_minutes: int = 15) -> Dict[str, Any]:
    """Compact funding snapshot for ``symbol``. Never raises."""
    state: Dict[str, Any] = {
        "symbol": symbol, "crowding": None, "regime_state": None,
        "sign_flip": None, "funding_bps": None, "stretch_score": None, "shift_bps": None,
    }
    try:
        raw = await get_cached_mcp_skill("compare_funding_rate_across_venues", {"symbol": symbol}, ttl_minutes=ttl_minutes)
        rep = _extract_report(raw)
        if rep:
            state["crowding"] = rep.get("pack_crowding_regime")
            ms = rep.get("most_stretched_venue", {}) or {}
            state["stretch_score"] = ms.get("stretch_score")
            state["funding_bps"] = ms.get("funding_rate_bps")
            agg = rep.get("aggregate_context", {}) or {}
            state["shift_bps"] = agg.get("window_funding_shift_bps")
    except Exception as e:
        logger.warning(f"compare_funding skill unavailable for {symbol}: {e}")
    try:
        raw = await get_cached_mcp_skill("detect_funding_rate_regime_shift", {"symbol": symbol}, ttl_minutes=ttl_minutes)
        rep = _extract_report(raw)
        if rep:
            state["regime_state"] = rep.get("regime_state")
            state["sign_flip"] = rep.get("sign_flip_detected")
            if state["shift_bps"] is None:
                state["shift_bps"] = rep.get("oi_weighted_shift_bps")
    except Exception as e:
        logger.warning(f"funding_regime skill unavailable for {symbol}: {e}")
    return state


def _is_extreme(state: Dict[str, Any]) -> bool:
    bps = abs(state.get("funding_bps") or 0.0)
    stretch = state.get("stretch_score") or 0.0
    return bps >= EXTREME_FUNDING_BPS or stretch >= EXTREME_STRETCH


def funding_confidence_mult(direction: str, state: Dict[str, Any]) -> Tuple[float, str]:
    """Confidence multiplier for a perp signal given the funding crowding.
    Fade the crowd: boost a signal that trades AGAINST an extreme crowd, suppress
    one that trades WITH it. Returns (multiplier, reason)."""
    crowd = str(state.get("crowding") or "").lower()
    if not _is_extreme(state) or "crowded" not in crowd:
        return 1.0, "funding neutral"
    direction = "short" if str(direction).lower() == "short" else "long"
    if "long" in crowd:   # crowded_longs -> fade = short
        if direction == "short":
            return 1.15, f"funding tailwind (fading crowded longs, {state.get('funding_bps')}bps)"
        return 0.6, f"funding headwind (long into crowded longs, {state.get('funding_bps')}bps)"
    if "short" in crowd:  # crowded_shorts -> fade = long
        if direction == "long":
            return 1.15, f"funding tailwind (fading crowded shorts, {state.get('funding_bps')}bps)"
        return 0.6, f"funding headwind (short into crowded shorts, {state.get('funding_bps')}bps)"
    return 1.0, "funding neutral"
