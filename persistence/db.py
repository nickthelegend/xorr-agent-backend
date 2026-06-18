import os
from pathlib import Path
from sqlalchemy import event, text
from sqlmodel import SQLModel, create_engine, Session, select
from typing import Generator
from persistence.models import (
    Trade, Position, EquityPoint, EngineLog, RuntimeState, CooldownEntry,
    StrategyStat, LLMVote, BacktestRun, McpSkillCache
)

# Path settings
DB_DIR = Path("data_store")
DB_PATH = DB_DIR / "trades.db"

# Ensure directory exists
DB_DIR.mkdir(parents=True, exist_ok=True)

# Engine setup
sqlite_url = f"sqlite:///{DB_PATH}"
connect_args = {"check_same_thread": False}
engine = create_engine(sqlite_url, connect_args=connect_args)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, conn_record):
    """Enable WAL + a busy timeout so the engine's short-lived ledger sessions
    don't collide with the long-lived pipeline/monitor sessions."""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


# Columns added after the first release. SQLModel.create_all does not ALTER
# existing tables, so we additively migrate any missing columns here.
_MIGRATIONS = {
    "runtimestate": [
        ("sim_cash_usdt", "FLOAT", "100.0"),
        ("sim_bnb", "FLOAT", "0.05"),
        ("sim_seeded", "BOOLEAN", "0"),
        ("registered", "BOOLEAN", "0"),
        ("registered_tx", "VARCHAR", "NULL"),
        ("start_equity", "FLOAT", "0.0"),
        ("risk_paused_until", "FLOAT", "0.0"),
    ],
    "trade": [
        ("entry_price", "FLOAT", "NULL"),
        ("exit_price", "FLOAT", "NULL"),
        ("direction", "VARCHAR", "'long'"),
        ("venue", "VARCHAR", "'spot'"),
        ("leverage", "FLOAT", "1.0"),
    ],
    "position": [
        ("is_perp", "BOOLEAN", "0"),
        ("venue", "VARCHAR", "'spot'"),
        ("direction", "VARCHAR", "'long'"),
        ("leverage", "FLOAT", "1.0"),
        ("margin_usd", "FLOAT", "0.0"),
        ("liquidation_price", "FLOAT", "0.0"),
        ("is_shadow", "BOOLEAN", "0"),
        ("init_stop", "FLOAT", "0.0"),
    ],
}


def _migrate_columns():
    """Adds any missing columns to existing tables (additive, non-destructive)."""
    with engine.connect() as conn:
        for table, cols in _MIGRATIONS.items():
            existing = {
                row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))
            }
            if not existing:
                continue  # table will be created fresh by create_all
            for name, sqltype, default in cols:
                if name not in existing:
                    conn.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {name} {sqltype} DEFAULT {default}")
                    )
        conn.commit()


def init_db():
    """Initializes tables and seeds default states."""
    SQLModel.metadata.create_all(engine)
    _migrate_columns()

    # Check if RuntimeState is seeded
    with Session(engine) as session:
        statement = select(RuntimeState).where(RuntimeState.id == 1)
        state = session.exec(statement).first()
        if not state:
            default_state = RuntimeState(
                id=1,
                mode="simulation",
                scheduler_state="IDLE",
                kill_armed=True,
                peak_equity=0.0
            )
            session.add(default_state)
            session.commit()

def get_session() -> Generator[Session, None, None]:
    """Session yield dependency for FastAPI."""
    with Session(engine) as session:
        yield session
