from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List

# Simple string stub to avoid python string type naming issues
string = str


@dataclass
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

@dataclass
class Quote:
    symbol: string
    price: float
    pct_1h: float
    pct_24h: float
    volume_24h: float
    market_cap: float
    last_updated: datetime

@dataclass
class MarketContext:
    timestamp: datetime
    fear_greed_value: int
    fear_greed_label: string
    btc_dominance: float
    total_market_cap_usd: float
    total_market_cap_change_24h: float
    bnb_price_usd: float
    quotes: Dict[str, Quote] = field(default_factory=dict)
    open_positions: List[Any] = field(default_factory=list)
    regime: string = "CHOP"

@dataclass
class Signal:
    symbol: string
    contract: string
    side: string  # "buy" | "sell"
    confidence: float  # 0.0 to 1.0
    stop_loss_pct: float
    take_profit_pct: float
    max_hold_min: int
    rationale: string
    strategy_name: string
    entry_type: string = ""
    news_title: Optional[string] = None

@dataclass
class ScoredSignal:
    signal: Signal
    score: float  # 0 - 100
    reasoning: string

@dataclass
class ExecutionResult:
    success: bool
    tx_hash: string
    executed_price: float
    amount_in: float
    amount_out: float
    status: string = "submitted"  # "submitted" | "confirmed" | "reverted"
    error: Optional[string] = None

@dataclass
class DecisionLog:
    id: string
    t: datetime
    symbol: string
    action: string  # "ENTER" | "SKIP" | "EXIT" | "HOLD" | "RESIZE" | "MODE_CHANGE"
    strategy: string
    filters_passed: List[string] = field(default_factory=list)
    filters_blocked: List[string] = field(default_factory=list)
    brain_score: float = 0.0
    reasoning: string = ""
    market_snapshot: Dict[str, float] = field(default_factory=dict)

# Simple string stub to avoid python string type naming issues
string = str
