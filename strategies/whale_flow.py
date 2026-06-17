from typing import Optional, List
import logging
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from data.tokens import resolve
from data.cmc_mcp import get_cached_mcp_skill
from config import settings

logger = logging.getLogger("xorr.strategies.whale_flow")

def calculate_ema(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0.0
    multiplier = 2.0 / (period + 1.0)
    ema = sum(prices[:period]) / period
    for val in prices[period:]:
        ema = (val - ema) * multiplier + ema
    return ema

class WhaleFlowStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("whale_flow")

    async def evaluate(self, symbol: str, candles_5m: list, candles_1h: list, market_ctx: MarketContext) -> Optional[Signal]:
        # Precondition: flow-driven breakout needs real momentum confluence
        from filters.confluence_score import gate_threshold
        if market_ctx.confluence < gate_threshold():
            return None

        if len(candles_5m) < 20 or len(candles_1h) < 20:
            return None

        # 1. Fetch whale net flow from MCP
        net_flow = 0.0
        try:
            data = await get_cached_mcp_skill("monitor_whale_transfer_anomalies", {"symbol": symbol, "window": "1d"})
            if isinstance(data, dict):
                net_flow = data.get("net_flow", data.get("net_flow_usd", 0.0))
                if not net_flow and "buys" in data and "sells" in data:
                    net_flow = data["buys"] - data["sells"]
        except Exception as e:
            logger.warning(f"Failed to fetch whale netflow inside strategy for {symbol}: {e}")
            return None

        # Require net flow >= $50K
        if net_flow < 50000.0:
            return None

        # 2. 5m price made a higher low in the past 30 min (last 6 candles)
        lows_5m = [c.low for c in candles_5m]
        lows_last_3 = lows_5m[-3:]
        lows_prev_3 = lows_5m[-6:-3]
        if min(lows_last_3) <= min(lows_prev_3):
            return None

        # 3. 1h price above 1h 50-EMA OR breaking out from consolidation
        closes_1h = [c.close for c in candles_1h]
        current_price = closes_1h[-1]
        ema_50_1h = calculate_ema(closes_1h, period=50)
        
        above_ema = current_price > ema_50_1h
        
        # Consolidation breakout check: current price broke out above high of last 10 bars
        highs_1h = [c.high for c in candles_1h]
        is_breakout = current_price > max(highs_1h[-11:-1])
        
        if not (above_ema or is_breakout):
            return None

        token_info = resolve(symbol)
        contract = token_info.contract if token_info else ""

        return Signal(
            symbol=symbol,
            contract=contract,
            side="buy",
            confidence=0.88,
            stop_loss_pct=2.0,  # 2% stop loss
            take_profit_pct=2.0,  # TP starts trailing at +2% (managed by monitor.py)
            max_hold_min=240,
            rationale=f"Whale flow. Positive whale netflow ${net_flow:,.2f} >= $50k. 5m higher lows, 1h price above 50-EMA or breakout.",
            strategy_name=self.name
        )
