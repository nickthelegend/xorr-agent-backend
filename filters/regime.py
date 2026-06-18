from typing import Tuple
from data.binance_klines import fetch_binance_klines
from data.fear_greed import get_fear_greed

def calculate_ema(prices: list, period: int = 20) -> float:
    if not prices:
        return 0.0
    if len(prices) < period:
        return prices[-1]
    multiplier = 2 / (period + 1)
    # Start with SMA for initial value
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = (p - ema) * multiplier + ema
    return ema

async def classify_market_regime() -> str:
    """Classifies the market regime into TREND_UP | TREND_DOWN | CHOP | RISK_OFF."""
    try:
        # Fetch 30 daily candles for BTC
        btc_candles = await fetch_binance_klines("BTCUSDT", interval="1d", limit=30)
        if not btc_candles or len(btc_candles) < 2:
            return "CHOP"
            
        close_prices = [c.close for c in btc_candles]
        current_close = close_prices[-1]
        prev_close = close_prices[-2]
        
        # calculate 1d return
        btc_1d_return = (current_close - prev_close) / prev_close
        
        # calculate 20-day EMA
        ema_20 = calculate_ema(close_prices, period=20)
        
        # Fetch Fear & Greed index
        fng_val = 50
        fng_data = await get_fear_greed()
        if fng_data:
            fng_val = fng_data.get("value", 50)
            
        # 1. RISK_OFF check
        # Fear & Greed < 25 AND BTC 24h < -3%
        if fng_val < 25 and btc_1d_return < -0.03:
            return "RISK_OFF"
            
        # 2. TREND_UP check
        # BTC daily close > 20-day EMA AND 1d return > 0.5%
        if current_close > ema_20 and btc_1d_return > 0.005:
            return "TREND_UP"
            
        # 3. TREND_DOWN check
        # BTC daily close < 20-day EMA AND 1d return < -0.5%
        if current_close < ema_20 and btc_1d_return < -0.005:
            return "TREND_DOWN"
            
        return "CHOP"
    except Exception as e:
        print(f"[REGIME WARNING] Failed to classify market regime: {e}")
        return "CHOP"

def is_actionable(regime: str, strategy_name: str) -> bool:
    """Returns True if the strategy is allowed to enter in the current regime."""
    s_name = strategy_name.lower()
    if "perp" in s_name:
        # The perp book is long/short and gates direction by regime INTERNALLY
        # (long in TREND_UP/CHOP, short in TREND_DOWN/RISK_OFF/CHOP). Let it run
        # in every regime so it can short the down tape the spot book can't touch.
        return True
    if "xsect" in s_name:
        # Cross-sectional relative-strength: trend-up or ranging, never confirmed downtrend
        return regime in ["TREND_UP", "CHOP"]
    if "momentum" in s_name:
        # Momentum pullbacks ONLY in a confirmed uptrend. (Allowing CHOP caused
        # heavy overtrading and bleed — backtest-validated regression.)
        return regime == "TREND_UP"
    elif "capitulation" in s_name:
        # Counter-trend bounce after a flush: most useful in weak/falling tape
        return regime in ["TREND_DOWN", "CHOP", "RISK_OFF"]
    elif "fib" in s_name:
        # Fibonacci pocket bounce works in TREND_UP or CHOP
        return regime in ["TREND_UP", "CHOP"]
    elif "news" in s_name:
        # News catalyst bypasses regime checks
        return True
    elif "donchian" in s_name or "breakout" in s_name:
        # Breakouts only make sense when not in a confirmed downtrend
        return regime in ["TREND_UP", "CHOP"]
    return True
