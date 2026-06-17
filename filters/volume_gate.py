import logging
from core.types import MarketContext
from data.binance_klines import fetch_binance_klines

logger = logging.getLogger("xorr.filters.volume_gate")

async def passes(ctx: MarketContext, symbol: str) -> bool:
    """
    Checks if a token passes the volume gate:
    1. 24h volume >= $500k.
    2. Current 5m volume >= 0.5x rolling 20-bar mean.
    """
    # 1. 24h Volume check
    quote = ctx.quotes.get(symbol.upper())
    if not quote:
        logger.warning(f"No quote found for {symbol} in volume gate")
        return False
        
    if quote.volume_24h < 500000.0:
        logger.info(f"[VOLUME GATE] {symbol} rejected: 24h volume ${quote.volume_24h:,.2f} < $500k")
        return False

    # 2. Volume regime check (5m bar)
    try:
        # fetch last 21 bars to calculate mean of preceding 20 bars
        candles_5m = await fetch_binance_klines(symbol, "5m", limit=25)
        if len(candles_5m) < 21:
            logger.info(f"[VOLUME GATE] {symbol} rejected: insufficient 5m candles ({len(candles_5m)})")
            return False
            
        current_vol = candles_5m[-1].volume
        preceding_vols = [c.volume for c in candles_5m[-21:-1]]
        rolling_mean = sum(preceding_vols) / len(preceding_vols)
        
        if rolling_mean == 0:
            return True
            
        if current_vol < 0.5 * rolling_mean:
            logger.info(f"[VOLUME GATE] {symbol} rejected: current 5m vol {current_vol:.1f} < 0.5x mean ({rolling_mean:.1f})")
            return False
            
        return True
    except Exception as e:
        logger.error(f"Failed to fetch or evaluate 5m volume regime for {symbol}: {e}")
        return False
