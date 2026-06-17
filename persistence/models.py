from sqlmodel import SQLModel, Field
from datetime import datetime
from typing import Optional

class Trade(SQLModel, table=True):
    id: str = Field(default=None, primary_key=True)
    opened_at: str
    closed_at: Optional[str] = None
    symbol: str
    contract: str
    status: str  # "open" | "win" | "loss" | "breakeven"
    invested: float
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    hold_minutes: float = 0.0
    entry_mc: Optional[float] = None
    exit_mc: Optional[float] = None
    score: float = 0.0
    exit_reason: Optional[str] = None
    window: str  # "COMPETITION" | "QUALIFIER"
    tx_open: str
    tx_close: Optional[str] = None
    strategy: str

class Position(SQLModel, table=True):
    id: str = Field(default=None, primary_key=True)  # transaction open hash
    symbol: str
    contract: str
    opened_at: float  # timestamp
    entry_price: float
    stop_loss: float
    take_profit: float
    size: float  # size in token units
    strategy: str
    invested: float  # USDT amount invested
    mode: str = "simulation"  # "simulation" | "live"
    tp1_hit: bool = False

class EquityPoint(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    t: str  # ISO string
    equity_usd: float

class EngineLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    t: str  # ISO string
    level: str
    msg: str

class RuntimeState(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    mode: str = "simulation"  # "simulation" | "live"
    scheduler_state: str = "IDLE"  # "IDLE" | "SCANNING" | "MONITORING" | "HALTED"
    kill_armed: bool = True
    peak_equity: float = 0.0

class CooldownEntry(SQLModel, table=True):
    symbol: str = Field(primary_key=True)
    until: float  # timestamp when cooldown expires
    reason: str
