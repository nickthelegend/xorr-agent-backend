from sqlmodel import Session
from persistence.repo import get_state, update_state
from config import settings

def calculate_drawdown_multiplier(session: Session, current_equity: float) -> float:
    """
    Computes current drawdown % from peak equity and returns a multiplier:
      - DD < 5%: 1.0
      - DD 5-10%: 0.75
      - DD 10-15%: 0.5
      - DD 15-20%: 0.25
      - DD > 20%: 0.0 (stop entries)
    """
    state = get_state(session)
    peak = state.peak_equity
    
    # Update peak equity if current is higher
    if current_equity > peak:
        update_state(session, peak_equity=current_equity)
        peak = current_equity
        
    if peak <= 0.0:
        return 1.0
        
    drawdown_pct = ((peak - current_equity) / peak) * 100.0
    
    # Check bounds
    if drawdown_pct < 5.0:
        return 1.0
    elif drawdown_pct < 10.0:
        return 0.75
    elif drawdown_pct < 15.0:
        return 0.5
    elif drawdown_pct < 20.0:
        return 0.25
    else:
        # DD > 20%
        return 0.0
