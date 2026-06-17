import httpx
import time
from typing import Optional, Tuple
from config import settings
from data.binance_klines import normalize_binance_symbol

# Cache: symbol -> (timestamp, binance_price, bybit_price)
_price_cache = {}
CACHE_EXPIRY_SEC = 10.0

async def fetch_binance_price(symbol: str) -> Optional[float]:
    binance_symbol = normalize_binance_symbol(symbol)
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={binance_symbol}"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                return float(data.get("price", 0.0))
    except Exception as e:
        print(f"[ORACLE WARNING] Failed to fetch Binance price for {binance_symbol}: {e}")
    return None

async def fetch_bybit_price(symbol: str) -> Optional[float]:
    bybit_symbol = normalize_binance_symbol(symbol)
    url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={bybit_symbol}"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(url)
            if r.status_code == 0 or r.status_code == 200:
                data = r.json()
                result = data.get("result", {})
                ticker_list = result.get("list", [])
                if ticker_list:
                    return float(ticker_list[0].get("lastPrice", 0.0))
    except Exception as e:
        print(f"[ORACLE WARNING] Failed to fetch Bybit price for {bybit_symbol}: {e}")
    return None

async def get_cex_prices(symbol: str) -> Tuple[Optional[float], Optional[float]]:
    now = time.time()
    if symbol in _price_cache:
        t, b_price, by_price = _price_cache[symbol]
        if now - t < CACHE_EXPIRY_SEC:
            return b_price, by_price
            
    # Fetch concurrently
    import asyncio
    b_task = fetch_binance_price(symbol)
    by_task = fetch_bybit_price(symbol)
    b_price, by_price = await asyncio.gather(b_task, by_task)
    
    _price_cache[symbol] = (now, b_price, by_price)
    return b_price, by_price

async def is_sane(symbol: str, cmc_price: float) -> bool:
    """Verifies that the CMC quote matches CEX prices within allowed basis points deviation."""
    if not cmc_price or cmc_price <= 0:
        return False
        
    b_price, by_price = await get_cex_prices(symbol)
    
    # We choose the CEX price that is available, or average them if both are available
    cex_price = None
    if b_price and by_price:
        cex_price = (b_price + by_price) / 2.0
    elif b_price:
        cex_price = b_price
    elif by_price:
        cex_price = by_price
        
    if not cex_price:
        # Fails open: if we cannot query CEX prices, we assume CMC is correct rather than halting trading
        print(f"[ORACLE] CEX feeds down for {symbol}. Fails open: price marked as sane.")
        return True
        
    deviation_bps = abs(cex_price - cmc_price) / cmc_price * 10000.0
    
    if deviation_bps > settings.cex_deviation_bps:
        print(f"[ORACLE REJECT] Glitch Shield triggered for {symbol}: CMC=${cmc_price:.6f}, CEX=${cex_price:.6f}. Deviation={deviation_bps:.1f} bps (Max limit={settings.cex_deviation_bps} bps)")
        return False
        
    return True
