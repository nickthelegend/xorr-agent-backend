import time
from datetime import datetime, timezone
from typing import Optional, List
from core.types import MarketContext, Signal
from strategies.base import BaseStrategy
from data.tokens import resolve
from config import settings

# Global list of recent news events populated by the news polling loop
# Each element: (symbol, title, timestamp)
recent_listing_events = []

class NewsCatalystStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("news_catalyst")

    async def evaluate(self, symbol: str, candles_5m: list, candles_1h: list, market_ctx: MarketContext) -> Optional[Signal]:
        # Precondition check
        threshold = settings.confluence_threshold if settings.quality_mode else 60
        if market_ctx.confluence < threshold:
            return None

        # News catalyst doesn't need technical candles; it evaluates on news events
        token_info = resolve(symbol)
        if not token_info:
            return None
            
        now = datetime.now(timezone.utc)
        
        # Check if there is an active news listing event for this symbol in the last 90 seconds
        for event_sym, title, ts in recent_listing_events:
            if event_sym.upper() == symbol.upper():
                age_sec = (now - ts).total_seconds()
                if age_sec < 90.0:
                    # Trigger entry signal!
                    # News catalyst is highly confident and doubles position sizing
                    return Signal(
                        symbol=symbol,
                        contract=token_info.contract,
                        side="buy",
                        confidence=0.95,
                        stop_loss_pct=3.0,  # Wider stop for news
                        take_profit_pct=5.0,  # 5.0% take profit target
                        max_hold_min=25,     # Short hold time (25 mins)
                        rationale=f"Binance announcement listing catalyst: {title} (age {age_sec:.1f}s)",
                        strategy_name=self.name,
                        entry_type="news",
                        news_title=title
                    )
        return None
