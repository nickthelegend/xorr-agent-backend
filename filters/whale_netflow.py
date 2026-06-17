import logging
from typing import Optional
from core.types import MarketContext
from data.cmc_mcp import get_cached_mcp_skill
from data.binance_klines import fetch_binance_klines

logger = logging.getLogger("xorr.filters.whale_netflow")

async def is_bullish(ctx: MarketContext, symbol: str) -> bool:
    """
    Returns True if the whale netflow is bullish for long strategies:
    - Net flow is NOT <= -5% of 24h volume.
    - Long bias requires net flow >= 0 OR price has bottomed (4h close > 4h open AND last 8 bars higher low).
    """
    quote = ctx.quotes.get(symbol.upper())
    if not quote:
        return False

    # 1. Fetch whale net flow from MCP
    net_flow = 0.0
    try:
        data = await get_cached_mcp_skill("monitor_whale_transfer_anomalies", {"symbol": symbol, "window": "1d"})
        if isinstance(data, dict):
            net_flow = data.get("net_flow", data.get("net_flow_usd", 0.0))
            if not net_flow and "buys" in data and "sells" in data:
                net_flow = data["buys"] - data["sells"]
    except Exception as e:
        logger.warning(f"Failed to fetch whale netflow from MCP for {symbol}: {e}. Falling back to neutral (0).")
        net_flow = 0.0

    # Skip if net flow <= -5% of 24h volume
    vol_24h = quote.volume_24h or 1.0
    flow_ratio = net_flow / vol_24h
    if flow_ratio <= -0.05:
        logger.info(f"[WHALE FLOW] {symbol} rejected: negative net flow ({flow_ratio:.1%}) <= -5% of 24h volume")
        return False

    # Check if long bias holds (net_flow >= 0 OR price has bottomed)
    if net_flow >= 0.0:
        return True

    # Otherwise check if price has bottomed
    try:
        candles_1h = await fetch_binance_klines(symbol, "1h", limit=30)
        if len(candles_1h) < 8:
            return False
            
        # 4h close > 4h open
        # Current close vs open of candle 4 bars ago (index -4)
        c_4h_bullish = candles_1h[-1].close > candles_1h[-4].open
        
        # Last 8 bars form higher lows
        lows = [c.low for c in candles_1h[-8:]]
        # Check if the lows are generally ascending (each low is >= previous low)
        hl = all(lows[i] >= lows[i-1] for i in range(1, len(lows)))
        
        price_bottomed = c_4h_bullish and hl
        if price_bottomed:
            logger.info(f"[WHALE FLOW] {symbol} has positive bias due to price bottom (net flow was negative: {net_flow:,.2f})")
            return True
            
        logger.info(f"[WHALE FLOW] {symbol} rejected: negative net flow ({net_flow:,.2f}) and no price bottom detected")
        return False
    except Exception as e:
        logger.error(f"Failed to calculate price bottom for {symbol}: {e}")
        return False
