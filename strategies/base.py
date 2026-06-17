from abc import ABC, abstractmethod
from typing import Optional
from core.types import MarketContext, Signal

class BaseStrategy(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def evaluate(self, symbol: str, candles_5m: list, candles_1h: list, market_ctx: MarketContext) -> Optional[Signal]:
        """
        Evaluates the strategy logic.
        Returns a Signal object if entry conditions are met, otherwise None.
        """
        pass
