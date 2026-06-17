import httpx
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
from config import settings
from core.types import Quote
from data.tokens import iter_all

_cmc_quotes_cache: Dict[str, Quote] = {}
_last_cmc_fetch_time = 0.0
CACHE_EXPIRY_SEC = 60.0

async def fetch_cmc_quotes() -> Dict[str, Quote]:
    """Fetches latest quotes for all whitelisted tokens from CoinMarketCap or public fallback."""
    global _cmc_quotes_cache, _last_cmc_fetch_time
    now = time.time()
    
    if _cmc_quotes_cache and (now - _last_cmc_fetch_time < CACHE_EXPIRY_SEC):
        return _cmc_quotes_cache

    tokens = iter_all()
    if not tokens:
        return {}

    symbols = [t.symbol for t in tokens]
    
    # If CMC key is empty, fall back to public CEX price sources to keep agent operational
    if not settings.cmc_api_key:
        print("[CMC] API key missing. Falling back to public CEX prices.")
        return await _fetch_fallback_quotes(symbols)

    url = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest"
    # Join symbols
    symbol_str = ",".join(symbols)
    headers = {
        "X-CMC_PRO_API_KEY": settings.cmc_api_key,
        "Accept": "application/json"
    }
    params = {
        "symbol": symbol_str,
        "convert": "USDT"
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(url, headers=headers, params=params)
            if response.status_code == 200:
                data = response.json()
                quotes_data = data.get("data", {})
                new_quotes = {}
                
                for sym in symbols:
                    sym_quotes = quotes_data.get(sym, [])
                    if not sym_quotes:
                        continue
                    
                    # CMC might return a list of tokens for the same symbol
                    # Find the one on BSC if available, otherwise use the first one
                    quote_item = sym_quotes[0]
                    # Get quote detail in USD/USDT
                    quote_usd = quote_item.get("quote", {}).get("USDT", {})
                    if not quote_usd:
                        quote_usd = quote_item.get("quote", {}).get("USD", {})
                        
                    price = float(quote_usd.get("price", 0.0))
                    pct_1h = float(quote_usd.get("percent_change_1h", 0.0))
                    pct_24h = float(quote_usd.get("percent_change_24h", 0.0))
                    volume = float(quote_usd.get("volume_24h", 0.0))
                    mc = float(quote_usd.get("market_cap", 0.0))
                    
                    new_quotes[sym.upper()] = Quote(
                        symbol=sym.upper(),
                        price=price,
                        pct_1h=pct_1h,
                        pct_24h=pct_24h,
                        volume_24h=volume,
                        market_cap=mc,
                        last_updated=datetime.now(timezone.utc)
                    )
                
                if new_quotes:
                    _cmc_quotes_cache.update(new_quotes)
                    _last_cmc_fetch_time = now
                    return _cmc_quotes_cache
            else:
                print(f"[CMC WARNING] CoinMarketCap returned status code {response.status_code}")
    except Exception as e:
        print(f"[CMC ERROR] Failed to fetch quotes from CoinMarketCap: {e}")

    # Fallback to public prices if CMC fails
    return await _fetch_fallback_quotes(symbols)

async def _fetch_fallback_quotes(symbols: List[str]) -> Dict[str, Quote]:
    """Fallback quote fetcher using public Binance endpoints."""
    # We will fetch prices for our top tokens. Since querying 149 individual tickers
    # takes a lot of time, we query the 24h ticker endpoint: https://api.binance.com/api/v3/ticker/24hr
    # This returns all tickers in a single call!
    url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(url)
            if response.status_code == 200:
                data = response.json()
                # Index by symbol for quick lookup
                ticker_map = {item["symbol"]: item for item in data}
                
                new_quotes = {}
                for sym in symbols:
                    binance_sym = sym.upper()
                    if binance_sym == "BTCB":
                        binance_sym = "BTC"
                    elif binance_sym == "WBNB":
                        binance_sym = "BNB"
                        
                    pair_name = f"{binance_sym}USDT"
                    if pair_name in ticker_map:
                        item = ticker_map[pair_name]
                        price = float(item.get("lastPrice", 0.0))
                        pct_24h = float(item.get("priceChangePercent", 0.0))
                        volume = float(item.get("volume", 0.0)) * price
                        # Approximate market cap if missing
                        mc = volume * 365 # heuristic
                        new_quotes[sym.upper()] = Quote(
                            symbol=sym.upper(),
                            price=price,
                            pct_1h=0.0,
                            pct_24h=pct_24h,
                            volume_24h=volume,
                            market_cap=mc,
                            last_updated=datetime.now(timezone.utc)
                        )
                if new_quotes:
                    _cmc_quotes_cache.update(new_quotes)
                    global _last_cmc_fetch_time
                    _last_cmc_fetch_time = time.time()
                    return _cmc_quotes_cache
    except Exception as e:
        print(f"[FALLBACK PRICE ERROR] Failed to fetch fallback tickers from Binance: {e}")
        
    return _cmc_quotes_cache
