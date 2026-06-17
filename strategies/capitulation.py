from typing import Optional
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from data.tokens import resolve

class CapitulationStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("capitulation")

    def evaluate(self, symbol: str, candles_5m: list, candles_1h: list, market_ctx: MarketContext) -> Optional[Signal]:
        if len(candles_1h) < 2 or len(candles_5m) < 20:
            return None
            
        closes_1h = [c.close for c in candles_1h]
        volumes_5m = [c.volume for c in candles_5m]
        
        # 1. 1h drop > 8% (either last 1h candle or rolling 2 hours)
        last_close_1h = closes_1h[-1]
        prev_close_1h = closes_1h[-2]
        h1_drop = (prev_close_1h - last_close_1h) / prev_close_1h
        
        # Also check last 2 hours for extra stability
        if len(closes_1h) >= 3:
            h2_drop = (closes_1h[-3] - last_close_1h) / closes_1h[-3]
            drop_satisfied = h1_drop > 0.08 or h2_drop > 0.08
        else:
            drop_satisfied = h1_drop > 0.08
            
        if not drop_satisfied:
            return None
            
        # 2. Current 5m volume > 3x rolling 20-bar mean volume on 5m
        current_vol = volumes_5m[-1]
        mean_vol = sum(volumes_5m[-21:-1]) / 20.0
        if mean_vol <= 0 or current_vol < 3 * mean_vol:
            return None
            
        # 3. Lower wick > 60% of total bar range on the current or last 5m candle
        # We check the last candle (the potential hammer)
        last_candle = candles_5m[-1]
        high = last_candle.high
        low = last_candle.low
        open_p = last_candle.open
        close_p = last_candle.close
        
        bar_range = high - low
        if bar_range <= 0:
            return None
            
        body_low = min(open_p, close_p)
        lower_wick = body_low - low
        lower_wick_pct = lower_wick / bar_range
        
        if lower_wick_pct <= 0.60:
            return None
            
        # If all conditions pass, buy the rebound
        token_info = resolve(symbol)
        contract = token_info.contract if token_info else ""
        
        return Signal(
            symbol=symbol,
            contract=contract,
            side="buy",
            confidence=0.85,
            stop_loss_pct=2.2,
            take_profit_pct=3.0,
            max_hold_min=60,  # Hard time-stop at 60 mins
            rationale="1h drop > 8%, 5m volume climax (> 3x mean), lower wick > 60% of range (hammer pattern).",
            strategy_name=self.name
        )
