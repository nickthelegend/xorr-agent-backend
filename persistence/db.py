import os
from pathlib import Path
from sqlmodel import SQLModel, create_engine, Session, select
from typing import Generator
from persistence.models import RuntimeState

# Path settings
DB_DIR = Path("data_store")
DB_PATH = DB_DIR / "trades.db"

# Ensure directory exists
DB_DIR.mkdir(parents=True, exist_ok=True)

# Engine setup
sqlite_url = f"sqlite:///{DB_PATH}"
connect_args = {"check_same_thread": False}
engine = create_engine(sqlite_url, connect_args=connect_args)

def init_db():
    """Initializes tables and seeds default states."""
    SQLModel.metadata.create_all(engine)
    
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
