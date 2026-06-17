from typing import Optional
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from data.tokens import resolve
from config import settings

class FibGoldenPocketStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("fib_golden_pocket")

    async def evaluate(self, symbol: str, candles_5m: list, candles_1h: list, market_ctx: MarketContext) -> Optional[Signal]:
        # Precondition: trend-following needs real momentum confluence
        from filters.confluence_score import gate_threshold
        if market_ctx.confluence < gate_threshold():
            return None

        if len(candles_5m) < 20:
            return None
            
        highs = [c.high for c in candles_5m]
        lows = [c.low for c in candles_5m]
        closes = [c.close for c in candles_5m]
        
        # 1. Scan for recent upward impulse leg on 5m:
        # A window of 8 bars within the last 16 candles where the price went up by > 2.5%
        impulse_detected = False
        leg_low = 0.0
        leg_high = 0.0
        leg_start_idx = 0
        leg_end_idx = 0
        
        # We scan rolling 8-bar windows from index -16 to -1
        for i in range(len(closes) - 16, len(closes) - 8):
            window_lows = lows[i : i + 8]
            window_highs = highs[i : i + 8]
            w_min = min(window_lows)
            w_max = max(window_highs)
            
            # Verify if it was a bullish move (close of window higher than open of window)
            w_open = closes[i]
            w_close = closes[i + 7]
            
            if w_close > w_open and (w_max - w_min) / w_min > 0.025:
                impulse_detected = True
                leg_low = w_min
                leg_high = w_max
                leg_start_idx = i
                leg_end_idx = i + 7
                break
                
        if not impulse_detected:
            return None
            
        # 2. Golden pocket boundaries (0.618 to 0.65 retracement)
        range_size = leg_high - leg_low
        gp_high = leg_high - 0.618 * range_size
        gp_low = leg_high - 0.65 * range_size
        
        # 3. Check if price has retraced into the pocket since the end of the leg
        touched_gp = False
        for low in lows[leg_end_idx:]:
            if gp_low <= low <= gp_high or low < gp_low:
                # low has touched or gone below the golden pocket
                touched_gp = True
                break
                
        if not touched_gp:
            return None
            
        # 4. Trigger: current 5m close back above 0.5 retracement
        level_05 = leg_high - 0.5 * range_size
        current_close = closes[-1]
        
        if current_close > level_05 and closes[-2] <= level_05:
            # First close back above 0.5 retracement
            # Stop loss just below 0.786 retracement
            level_0786 = leg_high - 0.786 * range_size
            stop_loss_pct = ((current_close - level_0786) / current_close) * 100.0
            
            # Bound stop loss between 1.0% and 3.0%
            stop_loss_pct = max(1.0, min(3.0, stop_loss_pct))
            
            # Take profit at the leg high
            take_profit_pct = ((leg_high - current_close) / current_close) * 100.0
            take_profit_pct = max(1.5, take_profit_pct)  # at least 1.5%
            
            token_info = resolve(symbol)
            contract = token_info.contract if token_info else ""
            
            return Signal(
                symbol=symbol,
                contract=contract,
                side="buy",
                confidence=0.8,
                stop_loss_pct=round(stop_loss_pct, 2),
                take_profit_pct=round(take_profit_pct, 2),
                max_hold_min=120,
                rationale=f"Fib 0.618 Golden Pocket bounce. Impulse leg ${leg_low:.4f} -> ${leg_high:.4f}. Entry above 0.5 retracement level.",
                strategy_name=self.name
            )
            
        return None
