from sqlmodel import Session
from persistence.repo import get_state

def get_current_mode(session: Session) -> str:
    """Returns the current execution mode: 'simulation' or 'live'."""
    state = get_state(session)
    return state.mode
