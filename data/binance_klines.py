import httpx
import time
from datetime import datetime, timezone
from typing import List, Dict, Tuple, Optional
from core.types import Candle

# Cache layout: (symbol, interval, limit) -> (timestamp, list of Candle)
_klines_cache: Dict[Tuple[str, str, int], Tuple[float, List[Candle]]] = {}
CACHE_DURATION_SEC = 30.0

def normalize_binance_symbol(symbol: str) -> str:
    sym = symbol.upper()
    if sym == "BTCB":
        return "BTCUSDT"
    if sym == "WBNB":
        return "BNBUSDT"
    if sym.endswith("USDT"):
        return sym
    return f"{sym}USDT"

async def fetch_binance_klines(symbol: str, interval: str = "5m", limit: int = 100) -> List[Candle]:
    """Fetches public Binance klines/candlesticks for a symbol and interval."""
    binance_symbol = normalize_binance_symbol(symbol)
    cache_key = (binance_symbol, interval, limit)
    now = time.time()
    
    # Check cache
    if cache_key in _klines_cache:
        cached_time, cached_val = _klines_cache[cache_key]
        if now - cached_time < CACHE_DURATION_SEC:
            return cached_val

    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": binance_symbol,
        "interval": interval,
        "limit": limit
    }
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                candles = []
                for k in data:
                    # Binance kline index map:
                    # 0: Open time
                    # 1: Open price
                    # 2: High price
                    # 3: Low price
                    # 4: Close price
                    # 5: Volume
                    ts_ms = k[0]
                    dt = datetime.fromtimestamp(ts_ms / 1000.0, timezone.utc)
                    candles.append(Candle(
                        ts=dt,
                        open=float(k[1]),
                        high=float(k[2]),
                        low=float(k[3]),
                        close=float(k[4]),
                        volume=float(k[5])
                    ))
                _klines_cache[cache_key] = (now, candles)
                return candles
            else:
                print(f"[KLINES WARNING] Binance returned non-200 code {response.status_code} for {binance_symbol}")
    except Exception as e:
        print(f"[KLINES ERROR] Failed to fetch Binance klines for {binance_symbol}: {e}")
        
    # Return from cache if we have stale cache, else empty list
    if cache_key in _klines_cache:
        return _klines_cache[cache_key][1]
    return []
