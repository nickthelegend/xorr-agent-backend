import numpy as np
from typing import Optional, List
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from data.tokens import resolve
from config import settings

def calculate_sma(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0.0
    return sum(prices[-period:]) / period

def calculate_ema(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0.0
    multiplier = 2.0 / (period + 1.0)
    ema = sum(prices[:period]) / period
    for val in prices[period:]:
        ema = (val - ema) * multiplier + ema
    return ema

def calculate_rsi(prices: List[float], period: int = 14) -> List[float]:
    if len(prices) < period + 1:
        return [50.0] * len(prices)
    
    rsi_values = []
    gains = []
    losses = []
    
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        if diff > 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))
            
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    if avg_loss == 0:
        rsi_values.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi_values.append(100.0 - (100.0 / (1.0 + rs)))
        
    for i in range(period, len(prices) - 1):
        diff = prices[i+1] - prices[i]
        gain = diff if diff > 0 else 0.0
        loss = abs(diff) if diff < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        
        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100.0 - (100.0 / (1.0 + rs)))
            
    # Pad to match original length
    padding = [50.0] * (len(prices) - len(rsi_values))
    return padding + rsi_values

class MeanReversionStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("mean_reversion")

    async def evaluate(self, symbol: str, candles_5m: list, candles_1h: list, market_ctx: MarketContext) -> Optional[Signal]:
        # Counter-trend: oversold BB/RSI bounces are intrinsically low-momentum, so
        # we do NOT require a high momentum-confluence score. The BB/RSI/volume
        # setup is the gate.
        if len(candles_5m) < 25 or len(candles_1h) < 55:
            return None

        closes_5m = [c.close for c in candles_5m]
        highs_5m = [c.high for c in candles_5m]
        lows_5m = [c.low for c in candles_5m]
        volumes_5m = [c.volume for c in candles_5m]
        closes_1h = [c.close for c in candles_1h]

        # 1. 1h Trend filter: price within +/- 3% of 1h 50-EMA
        ema_50_1h = calculate_ema(closes_1h, period=50)
        current_price = closes_5m[-1]
        dev = abs(current_price - ema_50_1h) / ema_50_1h
        if dev > 0.03:
            return None

        # 2. Bollinger Bands (20, 2std) on 5m
        bb_sma = calculate_sma(closes_5m, period=20)
        last_20_closes = closes_5m[-20:]
        bb_std = float(np.std(last_20_closes))
        lower_band = bb_sma - 2.0 * bb_std
        middle_band = bb_sma
        upper_band = bb_sma + 2.0 * bb_std

        # Price tagged or pierced lower band in the last 2 candles
        tagged = lows_5m[-1] <= lower_band or lows_5m[-2] <= lower_band
        if not tagged:
            return None

        # 3. RSI(14) on 5m crossed back above 30
        rsi_5m = calculate_rsi(closes_5m, period=14)
        # Crossed back above 30 means: current RSI > 30, and it was < 30 in the last 5 candles
        current_rsi = rsi_5m[-1]
        was_below_30 = any(val < 30.0 for val in rsi_5m[-5:-1])
        if current_rsi < 30.0 or not was_below_30:
            return None

        # 4. Volume bounce bar confirmation: volume > 1.5x rolling 20-bar mean of preceding 20 bars
        bounce_vol = volumes_5m[-1]
        preceding_vols = volumes_5m[-21:-1]
        mean_vol = sum(preceding_vols) / len(preceding_vols)
        if bounce_vol <= 1.5 * mean_vol:
            return None

        # Levels:
        # Stop loss: 0.4% below the low of the trigger bar
        trigger_low = lows_5m[-1]
        stop_loss_val = trigger_low * 0.996
        stop_loss_pct = ((current_price - stop_loss_val) / current_price) * 100.0
        stop_loss_pct = max(0.5, min(4.0, stop_loss_pct))  # safe bounds

        # Take profit percentage (upper Bollinger band)
        take_profit_pct = ((upper_band - current_price) / current_price) * 100.0
        take_profit_pct = max(1.0, min(10.0, take_profit_pct))

        token_info = resolve(symbol)
        contract = token_info.contract if token_info else ""

        return Signal(
            symbol=symbol,
            contract=contract,
            side="buy",
            confidence=0.70,
            stop_loss_pct=round(stop_loss_pct, 2),
            take_profit_pct=round(take_profit_pct, 2),
            max_hold_min=90,
            rationale=f"Mean reversion. Tagged 5m lower BB, RSI crossed above 30 ({current_rsi:.1f}), 1h 50-EMA deviation ({dev:.1%}), volume bounce 1.5x.",
            strategy_name=self.name
        )
