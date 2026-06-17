import numpy as np
from typing import Optional, List
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from data.tokens import resolve
from data.binance_klines import fetch_binance_klines
from config import settings

def calculate_ema(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0.0
    multiplier = 2.0 / (period + 1.0)
    ema = sum(prices[:period]) / period
    for val in prices[period:]:
        ema = (val - ema) * multiplier + ema
    return ema

def calculate_atr(candles: list, period: int = 14) -> float:
    tr_list = []
    for i in range(1, len(candles)):
        c = candles[i]
        prev_c = candles[i-1]
        tr = max(c.high - c.low, abs(c.high - prev_c.close), abs(c.low - prev_c.close))
        tr_list.append(tr)
    if not tr_list:
        return 0.0
    return sum(tr_list[-period:]) / min(len(tr_list), period)

class TrendFollowStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("trend_follow")

    async def evaluate(self, symbol: str, candles_5m: list, candles_1h: list, market_ctx: MarketContext) -> Optional[Signal]:
        # Precondition: trend-following needs real momentum confluence
        from filters.confluence_score import gate_threshold
        if market_ctx.confluence < gate_threshold():
            return None

        if len(candles_5m) < 20 or len(candles_1h) < 55:
            return None

        # Fetch daily candles
        try:
            candles_1d = await fetch_binance_klines(symbol, "1d", limit=60)
        except Exception:
            return None
            
        if len(candles_1d) < 55:
            return None

        closes_1d = [c.close for c in candles_1d]
        closes_1h = [c.close for c in candles_1h]
        closes_5m = [c.close for c in candles_5m]
        
        # 1. 1d: 20-EMA > 50-EMA AND price > 20-EMA
        ema_20_1d = calculate_ema(closes_1d, period=20)
        ema_50_1d = calculate_ema(closes_1d, period=50)
        current_price = closes_5m[-1]
        
        if ema_20_1d <= ema_50_1d or current_price <= ema_20_1d:
            return None

        # 2. 1h: 20-EMA > 50-EMA AND last 3 bars all closed above 20-EMA
        ema_20_1h = calculate_ema(closes_1h, period=20)
        ema_50_1h = calculate_ema(closes_1h, period=50)
        
        if ema_20_1h <= ema_50_1h:
            return None
            
        if not all(c.close > ema_20_1h for c in candles_1h[-3:]):
            return None

        # 3. 5m: price pulled back to within ATR(14) of 1h 20-EMA
        atr_1h = calculate_atr(candles_1h, period=14)
        if atr_1h <= 0:
            return None
            
        dist_to_ema_1h = abs(current_price - ema_20_1h)
        if dist_to_ema_1h > atr_1h:
            return None

        # 4. printed a bull engulfing or hammer on 5m
        c = candles_5m[-1]
        prev_c = candles_5m[-2]
        body = abs(c.close - c.open)
        range_ = c.high - c.low
        lower_wick = min(c.open, c.close) - c.low
        upper_wick = c.high - max(c.open, c.close)
        
        is_engulfing = (c.close > c.open) and (prev_c.close < prev_c.open) and (c.close >= prev_c.open) and (c.open <= prev_c.close)
        is_hammer = (lower_wick >= 2.0 * body) and (upper_wick <= 0.1 * range_) and (body > 0)
        
        if not (is_engulfing or is_hammer):
            return None

        # Levels:
        # Stop: 1.5 * ATR(14) of 1h below entry
        stop_loss_pct = (1.5 * atr_1h / current_price) * 100.0
        stop_loss_pct = max(1.0, min(5.0, stop_loss_pct))  # safe bounds

        # Set take profit as a default 3.0% (trailing stop is managed by monitor.py)
        take_profit_pct = 3.0

        token_info = resolve(symbol)
        contract = token_info.contract if token_info else ""

        return Signal(
            symbol=symbol,
            contract=contract,
            side="buy",
            confidence=0.85,
            stop_loss_pct=round(stop_loss_pct, 2),
            take_profit_pct=round(take_profit_pct, 2),
            max_hold_min=360,
            rationale=f"Trend follow. 1d & 1h MAs aligned. 5m pullback to 1h 20-EMA within 1 ATR ({atr_1h:.4f}), trigger candle engulfing={is_engulfing} hammer={is_hammer}.",
            strategy_name=self.name
        )
