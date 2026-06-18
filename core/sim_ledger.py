"""
Persistent paper-trading ledger for simulation mode.

In simulation mode there is no on-chain wallet, so the "cash" balance must live
in the database instead of in a TwakExecutor instance (the scheduler recreates
the executor on every tick, which previously wiped all simulated balances).

Cash (USDT) and the gas reserve (BNB) are stored on RuntimeState. Token holdings
are represented by the open Position rows, so a token's simulated balance is just
the sum of open position sizes for that contract. This keeps the ledger and the
position book in agreement and makes the boot reconciler a no-op in sim mode.
"""
from typing import List, Optional
from sqlmodel import Session, select
from persistence.db import engine
from persistence.models import RuntimeState, Position
from config import settings


def _get_state(session: Session) -> RuntimeState:
    state = session.get(RuntimeState, 1)
    if not state:
        state = RuntimeState(id=1)
        session.add(state)
        session.commit()
        session.refresh(state)
    return state


def ensure_seeded(starting_usdt: Optional[float] = None, starting_bnb: Optional[float] = None) -> None:
    """Seeds the paper ledger once, on first boot in simulation mode. Defaults to
    the configured sim_start_* so the paper book mirrors the live ~$60 wallet."""
    usdt = settings.sim_start_usdt if starting_usdt is None else starting_usdt
    bnb = settings.sim_start_bnb if starting_bnb is None else starting_bnb
    with Session(engine) as session:
        state = _get_state(session)
        if not state.sim_seeded:
            state.sim_cash_usdt = usdt
            state.sim_bnb = bnb
            state.sim_seeded = True
            session.add(state)
            session.commit()


def get_cash() -> float:
    with Session(engine) as session:
        return float(_get_state(session).sim_cash_usdt)


def get_bnb() -> float:
    with Session(engine) as session:
        return float(_get_state(session).sim_bnb)


def adjust_cash(delta: float) -> float:
    """Applies a signed delta to the paper USDT balance; never goes negative."""
    with Session(engine) as session:
        state = _get_state(session)
        state.sim_cash_usdt = max(0.0, float(state.sim_cash_usdt) + float(delta))
        session.add(state)
        session.commit()
        return float(state.sim_cash_usdt)


def adjust_bnb(delta: float) -> float:
    with Session(engine) as session:
        state = _get_state(session)
        state.sim_bnb = max(0.0, float(state.sim_bnb) + float(delta))
        session.add(state)
        session.commit()
        return float(state.sim_bnb)


def reset(starting_usdt: Optional[float] = None, starting_bnb: Optional[float] = None) -> None:
    """Resets the paper ledger to its starting balances (does not touch positions).
    Defaults to the configured sim_start_* (mirrors the live ~$60 wallet)."""
    usdt = settings.sim_start_usdt if starting_usdt is None else starting_usdt
    bnb = settings.sim_start_bnb if starting_bnb is None else starting_bnb
    with Session(engine) as session:
        state = _get_state(session)
        state.sim_cash_usdt = usdt
        state.sim_bnb = bnb
        state.sim_seeded = True
        session.add(state)
        session.commit()


def get_token_balance(contract: str) -> float:
    """Simulated balance of a SPOT token = sum of open spot position sizes for
    that contract. Perp positions hold no underlying token (only USDT margin),
    so they are excluded — counting their notional units would wrongly inflate
    the token balance."""
    if not contract:
        return 0.0
    target = contract.lower()
    with Session(engine) as session:
        positions = session.exec(select(Position)).all()
        return float(sum(
            p.size for p in positions
            if (p.contract or "").lower() == target and not getattr(p, "is_perp", False)
        ))


def list_open_positions() -> List[Position]:
    with Session(engine) as session:
        return list(session.exec(select(Position)).all())
