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
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    entry_mc: Optional[float] = None
    exit_mc: Optional[float] = None
    score: float = 0.0
    exit_reason: Optional[str] = None
    window: str  # "COMPETITION" | "QUALIFIER"
    tx_open: str
    tx_close: Optional[str] = None
    strategy: str
    direction: str = "long"   # "long" | "short"
    venue: str = "spot"       # "spot" | "perp"
    leverage: float = 1.0

class Position(SQLModel, table=True):
    id: str = Field(default=None, primary_key=True)  # transaction open hash
    symbol: str
    contract: str
    opened_at: float  # timestamp
    entry_price: float
    stop_loss: float
    take_profit: float
    size: float  # token units (spot: held tokens; perp: notional units = margin*lev/entry)
    strategy: str
    invested: float  # USDT at risk (spot: cost; perp: margin posted)
    mode: str = "simulation"  # "simulation" | "live"
    tp1_hit: bool = False
    # --- Perpetual-futures fields (is_perp=False => plain spot long) ---
    is_perp: bool = False
    venue: str = "spot"            # "spot" | "perp"
    direction: str = "long"        # "long" | "short"
    leverage: float = 1.0
    margin_usd: float = 0.0        # collateral posted (mirrors `invested` for perps)
    liquidation_price: float = 0.0
    is_shadow: bool = False        # paper-test position (no capital); strategy stored as shadow_<name>
    init_stop: float = 0.0         # initial stop price (never trailed) — for R-multiple stats

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
    start_equity: float = 0.0      # baseline equity captured at boot, for return%
    # --- Simulation paper-trading ledger (source of truth in sim mode) ---
    sim_cash_usdt: float = 100.0   # paper USDT cash balance
    sim_bnb: float = 0.05          # paper BNB (gas reserve / valuation)
    sim_seeded: bool = False       # whether the sim ledger has been initialized
    # --- Live competition registration ---
    registered: bool = False       # agent wallet registered on-chain for the competition
    registered_tx: Optional[str] = None
    # --- Risk: soft de-risk pause (set by the kill switch's recoverable tier) ---
    risk_paused_until: float = 0.0  # epoch seconds; while now < this, no NEW entries

class CooldownEntry(SQLModel, table=True):
    symbol: str = Field(primary_key=True)
    until: float  # timestamp when cooldown expires
    reason: str

class StrategyStat(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    strategy: str = Field(index=True)
    trade_id: str
    closed_at: datetime
    r_realized: float
    pnl_usd: float

class LLMVote(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    decision_id: str = Field(index=True)
    model: str
    score: float
    reasoning: str
    red_flags_json: str
    latency_ms: int

class BacktestRun(SQLModel, table=True):
    run_id: str = Field(primary_key=True)
    started_at: datetime
    ended_at: datetime
    window_days: int
    quality_mode: bool
    report_json: str  # full BacktestReport serialized

class McpSkillCache(SQLModel, table=True):
    unique_name: str = Field(primary_key=True)
    payload_json: str
    cached_at: datetime
