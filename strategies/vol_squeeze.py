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

def calculate_atr(candles: list, period: int = 20) -> List[float]:
    tr_list = []
    for i in range(len(candles)):
        if i == 0:
            tr_list.append(candles[0].high - candles[0].low)
        else:
            c = candles[i]
            prev_c = candles[i-1]
            tr = max(c.high - c.low, abs(c.high - prev_c.close), abs(c.low - prev_c.close))
            tr_list.append(tr)
            
    atr_values = []
    for i in range(len(candles)):
        if i < period:
            atr_values.append(sum(tr_list[:i+1]) / (i+1))
        else:
            # Wilder's smoothing or simple SMA for simplicity
            atr_values.append(sum(tr_list[i-period+1 : i+1]) / period)
    return atr_values

class VolSqueezeStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("vol_squeeze")

    async def evaluate(self, symbol: str, candles_5m: list, candles_1h: list, market_ctx: MarketContext) -> Optional[Signal]:
        # Precondition: breakout needs real momentum confluence
        from filters.confluence_score import gate_threshold
        if market_ctx.confluence < gate_threshold():
            return None

        if len(candles_5m) < 30:
            return None

        closes_5m = [c.close for c in candles_5m]
        volumes_5m = [c.volume for c in candles_5m]
        
        # Calculate ATR(20) on 5m
        atr_5m_list = calculate_atr(candles_5m, period=20)
        
        # Calculate Bollinger Bands and Keltner Channels for each bar in the last 10 bars
        squeeze_history = []
        keltner_channels = []  # stores (lower, upper, range)
        
        for i in range(len(closes_5m) - 10, len(closes_5m)):
            sub_closes = closes_5m[:i+1]
            sub_candles = candles_5m[:i+1]
            
            # BB
            sma = calculate_sma(sub_closes, period=20)
            std = float(np.std(sub_closes[-20:]))
            bb_upper = sma + 2.0 * std
            bb_lower = sma - 2.0 * std
            
            # Keltner
            ema = calculate_ema(sub_closes, period=20)
            atr = atr_5m_list[i]
            kc_upper = ema + 1.5 * atr
            kc_lower = ema - 1.5 * atr
            
            keltner_channels.append((kc_lower, kc_upper, kc_upper - kc_lower))
            
            # Inside squeeze check
            is_squeeze = (bb_upper <= kc_upper) and (bb_lower >= kc_lower)
            squeeze_history.append(is_squeeze)

        # 1. Bollinger inside Keltner for at least 6 consecutive bars prior to the current bar
        # That is, index -7 to -2 (representing preceding bars in the last 10)
        squeeze_prior = squeeze_history[:-1] # exclude current bar
        # Find if there was a run of 6 consecutive squeezes
        consecutive_squeeze_count = 0
        max_consecutive = 0
        for sq in squeeze_prior:
            if sq:
                consecutive_squeeze_count += 1
                max_consecutive = max(max_consecutive, consecutive_squeeze_count)
            else:
                consecutive_squeeze_count = 0
                
        if max_consecutive < 6:
            return None

        # 2. Breakout: current bar closes above upper Keltner channel
        current_close = closes_5m[-1]
        kc_lower_curr, kc_upper_curr, kc_range_curr = keltner_channels[-1]
        
        if current_close <= kc_upper_curr:
            return None

        # 3. Volume on breakout > 2x rolling 20-bar mean of preceding volumes
        breakout_vol = volumes_5m[-1]
        preceding_vols = volumes_5m[-21:-1]
        mean_vol = sum(preceding_vols) / len(preceding_vols)
        if breakout_vol <= 2.0 * mean_vol:
            return None

        # Levels:
        # Stop: just below the lower Keltner channel at breakout
        stop_loss_pct = ((current_price := current_close) - kc_lower_curr) / current_price * 100.0
        stop_loss_pct = max(1.0, min(4.0, stop_loss_pct))
        
        # TP: measured move = upper Keltner - lower Keltner at squeeze peak, added to breakout level
        # Let's find Keltner range at squeeze peak (which was when the squeeze was tightest, i.e. min kc range)
        # We can approximate with the minimum kc range in the lookback window
        min_kc_range = min(kc[2] for kc in keltner_channels[:-1])
        take_profit_pct = (min_kc_range / current_price) * 100.0
        take_profit_pct = max(1.5, min(8.0, take_profit_pct))

        token_info = resolve(symbol)
        contract = token_info.contract if token_info else ""

        return Signal(
            symbol=symbol,
            contract=contract,
            side="buy",
            confidence=0.82,
            stop_loss_pct=round(stop_loss_pct, 2),
            take_profit_pct=round(take_profit_pct, 2),
            max_hold_min=45,
            rationale=f"Volatility squeeze breakout. Squeeze max consecutive={max_consecutive} bars. Close above Keltner Channel upper (${kc_upper_curr:.4f}), volume bounce {breakout_vol/mean_vol:.1f}x.",
            strategy_name=self.name
        )
