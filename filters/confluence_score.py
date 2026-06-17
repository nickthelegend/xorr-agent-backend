import numpy as np
import pandas as pd
import ta
import logging
from typing import Tuple, Dict
from datetime import datetime, timezone

from core.types import MarketContext
from data.binance_klines import fetch_binance_klines
from config import settings

logger = logging.getLogger("xorr.filters.confluence")

async def confluence_score(ctx: MarketContext, symbol: str) -> Tuple[int, Dict[str, float]]:
    """
    Computes a multi-factor composite confluence score (0-100) for a symbol.
    Returns (total_score, component_breakdown).
    """
    breakdown = {
        "momentum": 0.0,
        "technical": 0.0,
        "range_position": 0.0
    }
    
    # Fetch klines
    try:
        candles_5m = await fetch_binance_klines(symbol, "5m", limit=35)
        candles_1h = await fetch_binance_klines(symbol, "1h", limit=30)
    except Exception as e:
        logger.error(f"Failed to fetch klines for confluence of {symbol}: {e}")
        return 0, breakdown

    if not candles_5m or not candles_1h:
        logger.warning(f"No klines found for {symbol}")
        return 0, breakdown

    current_price = candles_1h[-1].close

    # --- 1. Momentum Component (Max 40 pts) ---
    mom_score = 0.0
    pct_1h_vals = [q.pct_1h for q in ctx.quotes.values() if q.pct_1h is not None]
    pct_24h_vals = [q.pct_24h for q in ctx.quotes.values() if q.pct_24h is not None]
    
    quote = ctx.quotes.get(symbol.upper())
    if quote:
        # 1h % change z-score
        z_1h = 0.0
        if pct_1h_vals and len(pct_1h_vals) > 1:
            med_1h = np.median(pct_1h_vals)
            std_1h = np.std(pct_1h_vals)
            if std_1h > 0:
                z_1h = (quote.pct_1h - med_1h) / std_1h
        
        # 24h % change z-score
        z_24h = 0.0
        if pct_24h_vals and len(pct_24h_vals) > 1:
            med_24h = np.median(pct_24h_vals)
            std_24h = np.std(pct_24h_vals)
            if std_24h > 0:
                z_24h = (quote.pct_24h - med_24h) / std_24h
                
        # Combine z-scores
        z_avg = (z_1h + z_24h) / 2.0
        mom_score = float(np.clip(20.0 + z_avg * 10.0, 0.0, 40.0))
    else:
        mom_score = 0.0

    breakdown["momentum"] = mom_score

    # --- 2. Technical Strength Component (Max 30 pts) ---
    tech_points = 0.0
    max_possible = 0.0

    # a. RSI(14) on 5m close in [40, 70] (15 pts)
    try:
        closes_5m = [c.close for c in candles_5m]
        df_5m = pd.DataFrame({"close": closes_5m})
        rsi_series = ta.momentum.rsi(df_5m["close"], window=14)
        if not rsi_series.empty and not pd.isna(rsi_series.iloc[-1]):
            rsi_val = rsi_series.iloc[-1]
            max_possible += 15.0
            if 40.0 <= rsi_val <= 70.0:
                tech_points += 15.0
    except Exception as e:
        logger.warning(f"RSI calculation failed for {symbol}: {e}")

    # b. Price above 20-EMA on 1h (10 pts)
    try:
        closes_1h = [c.close for c in candles_1h]
        df_1h = pd.DataFrame({"close": closes_1h})
        ema_series = ta.trend.ema_indicator(df_1h["close"], window=20)
        if not ema_series.empty and not pd.isna(ema_series.iloc[-1]):
            ema_val = ema_series.iloc[-1]
            max_possible += 10.0
            if current_price > ema_val:
                tech_points += 10.0
    except Exception as e:
        logger.warning(f"EMA calculation failed for {symbol}: {e}")

    # c. Positive MACD histogram on 5m (5 pts)
    try:
        macd_hist_series = ta.trend.macd_diff(df_5m["close"], window_fast=12, window_slow=26, window_sign=9)
        if not macd_hist_series.empty and not pd.isna(macd_hist_series.iloc[-1]):
            macd_hist_val = macd_hist_series.iloc[-1]
            max_possible += 5.0
            if macd_hist_val > 0:
                tech_points += 5.0
    except Exception as e:
        logger.warning(f"MACD calculation failed for {symbol}: {e}")

    # Proportional weight redistribution for Technical Strength
    if max_possible > 0:
        tech_score = (tech_points / max_possible) * 30.0
    else:
        tech_score = 0.0

    breakdown["technical"] = tech_score

    # --- 3. Range Position Component (Max 30 pts) ---
    range_score = 0.0
    try:
        candles_24h = candles_1h[-24:] if len(candles_1h) >= 24 else candles_1h
        high_24h = max(c.high for c in candles_24h)
        low_24h = min(c.low for c in candles_24h)
        
        if high_24h == low_24h:
            range_pct = 0.5
        else:
            range_pct = (current_price - low_24h) / (high_24h - low_24h)
            
        if 0.50 <= range_pct <= 0.70:
            range_score = 30.0
        elif range_pct > 0.70:
            if range_pct <= 0.90:
                range_score = 30.0 - ((range_pct - 0.70) / 0.20) * 15.0
            else:
                range_score = max(0.0, 15.0 - ((range_pct - 0.90) / 0.10) * 15.0)
        else:  # range_pct < 0.50
            if range_pct >= 0.10:
                range_score = 30.0 - ((0.50 - range_pct) / 0.40) * 20.0
            else:
                range_score = max(0.0, 10.0 - ((0.10 - range_pct) / 0.10) * 10.0)
    except Exception as e:
        logger.warning(f"Range position calculation failed for {symbol}: {e}")
        # If range is missing, range_score is 0, but we redistribute it to momentum/tech
        range_score = 0.0

    breakdown["range_position"] = range_score

    # Compute total raw score
    total_score = mom_score + tech_score + range_score
    
    # If range position was completely missing/failed, redistribute its weight proportionally
    # to Momentum (40 pts -> 40/70) and Technical (30 pts -> 30/70)
    # (Checking if max_possible high/low calculation worked - here we check if range_score is exactly 0.0 due to exception)
    # Actually, the spec says "If a sub-component data point is missing, redistribute its weight..."
    # Since we defined Momentum and Technical to handle their own subcomponents, let's keep it simple:
    # Sum the scores. If range is missing, we scale the sum of mom and tech by 100/70.
    # We will do this if range_pct could not be calculated (e.g. range_score remains 0 because of exception).
    # To be precise, if high_24h/low_24h raised exception:
    if 'range_pct' not in locals():
        # Redistribute 30 pts of range position
        # Present components: momentum (max 40) + tech (max 30) = 70.
        total_score = (mom_score + tech_score) * (100.0 / 70.0)

    return int(round(total_score)), breakdown

def gate_threshold() -> int:
    """Shared confluence gate threshold (quality vs relaxed). Used as the candidate
    junk-filter and by trend-following strategies' internal precondition."""
    return settings.confluence_threshold if settings.quality_mode else settings.confluence_threshold_relaxed


async def passes(ctx: MarketContext, symbol: str) -> bool:
    """Candidate junk-filter. Computes & caches the confluence score and rejects
    only clearly dead tokens (low floor). Trend strategies apply the higher
    gate_threshold() internally; counter-trend strategies define their own setups."""
    score, breakdown = await confluence_score(ctx, symbol)
    threshold = settings.confluence_junk_floor
    passed = score >= threshold
    if passed:
        logger.info(f"[CONFLUENCE] {symbol} passed with score {score} (threshold={threshold})")
    # Cache confluence on ctx for later use by strategies
    ctx.confluence = score
    return passed
