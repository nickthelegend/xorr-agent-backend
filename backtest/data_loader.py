import os
import time
import httpx
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta, timezone
from data.tokens import iter_all
from data.binance_klines import normalize_binance_symbol

CACHE_DIR = Path("data_store/klines")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

async def download_klines(symbol: str, interval: str, days: int = 90) -> pd.DataFrame:
    binance_symbol = normalize_binance_symbol(symbol)
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)
    
    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)
    
    url = "https://api.binance.com/api/v3/klines"
    all_data = []
    
    current_start = start_ms
    async with httpx.AsyncClient(timeout=15.0) as client:
        while current_start < end_ms:
            params = {
                "symbol": binance_symbol,
                "interval": interval,
                "startTime": current_start,
                "endTime": end_ms,
                "limit": 1000
            }
            try:
                response = await client.get(url, params=params)
                if response.status_code == 400:
                    # Symbol not found or invalid on Binance
                    print(f"[BACKTEST DOWNLOAD] Symbol {binance_symbol} not supported on Binance. Skipping.")
                    return pd.DataFrame()
                response.raise_for_status()
                data = response.json()
                if not data:
                    break
                all_data.extend(data)
                
                # The last returned kline open time
                last_time = data[-1][0]
                if last_time <= current_start:
                    break
                current_start = last_time + 1
                # Small throttle
                time.sleep(0.05)
            except Exception as e:
                print(f"[BACKTEST DOWNLOAD ERROR] Failed to fetch {binance_symbol} {interval}: {e}")
                break
                
    if not all_data:
        return pd.DataFrame()
        
    # Build dataframe
    # 0: Open time, 1: Open, 2: High, 3: Low, 4: Close, 5: Volume
    df = pd.DataFrame(all_data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
    ])
    
    df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
    df["open_time"] = df["open_time"].astype(int)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
        
    # Remove duplicates if any
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    return df

async def get_klines_for_backtest(symbol: str, interval: str, days: int = 90, force_refresh: bool = False) -> pd.DataFrame:
    cache_file = CACHE_DIR / f"{symbol}_{interval}.parquet"
    if cache_file.exists() and not force_refresh:
        try:
            df = pd.read_parquet(cache_file)
            if not df.empty:
                end_time = datetime.now(timezone.utc)
                start_time = end_time - timedelta(days=days)
                min_ts = int(start_time.timestamp() * 1000)
                # Check if cache covers start range (allow a small gap, e.g. 2 hours)
                if df["open_time"].min() <= min_ts + 7200000:
                    return df
        except Exception as e:
            print(f"[BACKTEST CACHE ERROR] Reading cache for {symbol} failed: {e}")
            
    # Fetch and cache
    print(f"[BACKTEST DATA] Downloading {symbol} {interval} for past {days} days...")
    df = await download_klines(symbol, interval, days)
    if not df.empty:
        df.to_parquet(cache_file, index=False)
        print(f"[BACKTEST DATA] Cached {len(df)} bars to {cache_file}")
    return df
