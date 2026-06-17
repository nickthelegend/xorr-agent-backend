from typing import Optional, List
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from data.tokens import resolve
from filters.regime import calculate_ema
from config import settings

def calculate_rsi(prices: List[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
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
        return 100.0
        
    for i in range(period, len(prices) - 1):
        diff = prices[i+1] - prices[i]
        gain = diff if diff > 0 else 0.0
        loss = abs(diff) if diff < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        
    rs = avg_gain / avg_loss if avg_loss > 0 else 0.0
    return 100 - (100 / (1 + rs)) if avg_loss > 0 else 100.0

class MomentumPullbackStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("momentum_pullback")

    async def evaluate(self, symbol: str, candles_5m: list, candles_1h: list, market_ctx: MarketContext) -> Optional[Signal]:
        # Precondition: trend-following needs real momentum confluence
        from filters.confluence_score import gate_threshold
        if market_ctx.confluence < gate_threshold():
            return None

        # Need enough history: at least 30 candles
        if len(candles_1h) < 20 or len(candles_5m) < 20:
            return None
            
        closes_1h = [c.close for c in candles_1h]
        closes_5m = [c.close for c in candles_5m]
        lows_5m = [c.low for c in candles_5m]
        
        # 1. 1h close above 1h 20-EMA
        ema_1h = calculate_ema(closes_1h, period=20)
        if closes_1h[-1] <= ema_1h:
            return None
            
        # 2. 5m EMA20
        ema_5m_list = []
        for i in range(20, len(closes_5m) + 1):
            ema_5m_list.append(calculate_ema(closes_5m[:i], period=20))
            
        # Check if 5m printed low at-or-below 20-EMA within last 30 minutes (last 6 candles)
        pullback_detected = False
        # The 20-EMA values matching the last 6 candles
        last_ema_5m = ema_5m_list[-6:]
        last_lows_5m = lows_5m[-6:]
        
        for low, ema in zip(last_lows_5m, last_ema_5m):
            if low <= ema:
                pullback_detected = True
                break
                
        if not pullback_detected:
            return None
            
        # 3. Higher low confirmation in the last 6 candles:
        # Lowest low in the last 3 candles is higher than the lowest low of the 3 candles before that
        lows_last_3 = lows_5m[-3:]
        lows_prev_3 = lows_5m[-6:-3]
        if min(lows_last_3) <= min(lows_prev_3):
            return None
            
        # 4. RSI(14) 5m > 45
        rsi_5m = calculate_rsi(closes_5m, period=14)
        if rsi_5m <= 45:
            return None
            
        # If all conditions pass, emit buy signal
        token_info = resolve(symbol)
        contract = token_info.contract if token_info else ""
        
        return Signal(
            symbol=symbol,
            contract=contract,
            side="buy",
            confidence=0.75,
            stop_loss_pct=1.8,
            take_profit_pct=2.0,  # first profit lock at +2%, then trailing 1.5%
            max_hold_min=180,
            rationale="1h close above 20-EMA, 5m pullback to 20-EMA with higher low confirmation, RSI(14)>45.",
            strategy_name=self.name
        )
