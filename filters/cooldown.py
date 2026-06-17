from sqlmodel import Session
from persistence.repo import add_cooldown, check_cooldown

def is_blacklisted(session: Session, symbol: str) -> bool:
    """Checks if a token is under active cooldown."""
    entry = check_cooldown(session, symbol)
    if entry:
        return True
    return False

def apply_cooldown(session: Session, symbol: str, outcome: str, hold_minutes: float):
    """
    Applies a retrade cooldown to a token based on trade outcomes:
      - On loss: 180 min cooldown (3 hours).
      - On stop-out within 10 minutes: 360 min cooldown (6 hours).
      - On win: 45 min cooldown (0.75 hours).
    """
    duration_sec = 0.0
    reason = ""
    
    if outcome == "loss":
        if hold_minutes <= 10.0:
            duration_sec = 360 * 60.0  # 360 minutes
            reason = "Fast stop-out loss (<= 10 mins)"
        else:
            duration_sec = 180 * 60.0  # 180 minutes
            reason = "Standard trade loss"
    elif outcome == "win":
        duration_sec = 45 * 60.0  # 45 minutes
        reason = "Profit lock cooldown"
    else:
        # Breakeven or neutral exit: 15 min cooling
        duration_sec = 15 * 60.0
        reason = "Neutral trade cooldown"

    if duration_sec > 0:
        add_cooldown(session, symbol, duration_sec, reason)
        print(f"[COOLDOWN] Applied {reason} to {symbol} for {duration_sec/60:.1f} minutes.")
