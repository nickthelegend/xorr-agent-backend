"""
Live macro signals from the CoinMarketCap skills marketplace (MCP).

Pulls a couple of marketplace skills and distills them into a compact dict that
feeds the LLM brain council (via MarketContext.macro) and can gate risk. Cached
via get_cached_mcp_skill (30-min TTL) and fully fail-open: any error returns the
partial/empty dict so the trading loop never blocks on the marketplace.

These are LIVE-only signals — they have no historical series we could replay, so
they intentionally do not participate in backtests.
"""
import json
import logging
from typing import Any, Dict

from data.cmc_mcp import get_cached_mcp_skill

logger = logging.getLogger("xorr.data.cmc_signals")


def _extract_report(raw: Dict[str, Any]) -> Dict[str, Any]:
    """The marketplace wraps results as {result:{output:"<json string>"}} where the
    inner JSON is {skill, result:{data:{report:{...}}}}. Be defensive about shape."""
    if not isinstance(raw, dict):
        return {}
    # Unwrap the stringified inner payload if present
    out = raw.get("result", {}).get("output") if isinstance(raw.get("result"), dict) else None
    parsed = None
    if isinstance(out, str):
        try:
            parsed = json.loads(out)
        except Exception:
            parsed = None
    if parsed is None:
        parsed = raw
    try:
        return parsed.get("result", {}).get("data", {}).get("report", {}) or {}
    except Exception:
        return {}


async def get_macro_signals() -> Dict[str, Any]:
    """Returns a compact macro snapshot for the brain context. Never raises."""
    macro: Dict[str, Any] = {}

    try:
        raw = await get_cached_mcp_skill("monitor_market_sentiment_shift", {}, ttl_minutes=30)
        rep = _extract_report(raw)
        if rep:
            macro["sentiment_regime"] = rep.get("sentiment_regime")
            macro["sentiment_inflection"] = rep.get("inflection_direction") or rep.get("inflection")
    except Exception as e:
        logger.warning(f"sentiment skill unavailable: {e}")

    try:
        raw = await get_cached_mcp_skill("assess_liquidation_cascade_risk", {}, ttl_minutes=30)
        rep = _extract_report(raw)
        if rep:
            macro["cascade_risk"] = rep.get("cascade_risk")
            macro["directional_pressure"] = rep.get("directional_pressure")
    except Exception as e:
        logger.warning(f"liquidation-risk skill unavailable: {e}")

    return macro


def is_macro_risk_off(macro: Dict[str, Any]) -> bool:
    """Heuristic risk-off flag from the macro snapshot — true when the marketplace
    explicitly flags an elevated/high liquidation cascade or a bearish sentiment shift."""
    cascade = str(macro.get("cascade_risk", "")).lower()
    sentiment = str(macro.get("sentiment_regime", "")).lower()
    if any(k in cascade for k in ("elevated", "high", "severe")):
        return True
    if any(k in sentiment for k in ("risk_off", "bearish", "capitulation", "deleverage")):
        return True
    return False
