import time
from sqlmodel import Session, select, delete
from typing import List, Optional
from persistence.models import Trade, Position, EquityPoint, EngineLog, RuntimeState, CooldownEntry

def get_state(session: Session) -> RuntimeState:
    state = session.get(RuntimeState, 1)
    if not state:
        state = RuntimeState(id=1, mode="simulation", scheduler_state="IDLE")
        session.add(state)
        session.commit()
        session.refresh(state)
    return state

def update_state(session: Session, **kwargs) -> RuntimeState:
    state = get_state(session)
    for k, v in kwargs.items():
        if hasattr(state, k):
            setattr(state, k, v)
    session.add(state)
    session.commit()
    session.refresh(state)
    return state

def get_positions(session: Session) -> List[Position]:
    statement = select(Position)
    return list(session.exec(statement).all())

def add_position(session: Session, position: Position) -> Position:
    session.add(position)
    session.commit()
    session.refresh(position)
    return position

def remove_position(session: Session, pos_id: str) -> bool:
    pos = session.get(Position, pos_id)
    if pos:
        session.delete(pos)
        session.commit()
        return True
    return False

def get_trades(session: Session, window: str = "all") -> List[Trade]:
    statement = select(Trade)
    if window == "competition":
        statement = statement.where(Trade.window == "COMPETITION")
    elif window == "qualifier":
        statement = statement.where(Trade.window == "QUALIFIER")
    # Sort by opened_at descending
    trades = list(session.exec(statement).all())
    trades.sort(key=lambda x: x.opened_at, reverse=True)
    return trades

def add_trade(session: Session, trade: Trade) -> Trade:
    session.add(trade)
    session.commit()
    session.refresh(trade)
    return trade

def add_equity_point(session: Session, equity: float) -> EquityPoint:
    pt = EquityPoint(t=datetime_iso(), equity_usd=equity)
    session.add(pt)
    session.commit()
    session.refresh(pt)
    return pt

def get_equity_history(session: Session, limit: int = 336) -> List[EquityPoint]:
    # 14 days * 24 points/day = 336 points
    statement = select(EquityPoint).order_by(EquityPoint.id.desc()).limit(limit)
    res = list(session.exec(statement).all())
    res.reverse()
    return res

def add_engine_log(session: Session, level: str, msg: str) -> EngineLog:
    log_entry = EngineLog(t=datetime_iso(), level=level, msg=msg)
    session.add(log_entry)
    session.commit()
    session.refresh(log_entry)
    return log_entry

def get_engine_logs(session: Session, limit: int = 100) -> List[EngineLog]:
    statement = select(EngineLog).order_by(EngineLog.id.desc()).limit(limit)
    res = list(session.exec(statement).all())
    res.reverse()
    return res

# Cooldown checks
def add_cooldown(session: Session, symbol: str, duration_sec: float, reason: str):
    until = time.time() + duration_sec
    entry = CooldownEntry(symbol=symbol, until=until, reason=reason)
    session.merge(entry)  # overwrites if symbol already exits
    session.commit()

def check_cooldown(session: Session, symbol: str) -> Optional[CooldownEntry]:
    # First clean up expired entries
    clean_expired_cooldowns(session)
    return session.get(CooldownEntry, symbol)

def clean_expired_cooldowns(session: Session):
    now = time.time()
    statement = delete(CooldownEntry).where(CooldownEntry.until < now)
    session.exec(statement)
    session.commit()

def datetime_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
