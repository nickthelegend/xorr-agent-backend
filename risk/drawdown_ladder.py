from sqlmodel import Session
from persistence.repo import get_state, update_state
from config import settings

def calculate_drawdown_multiplier(session: Session, current_equity: float) -> float:
    """Position-size multiplier that scales DOWN as drawdown grows, so risk is
    cut long before the competition's ~30% disqualification cap. The "stop new
    entries" rung is pinned a few points under the kill/flatten threshold
    (settings.flatten_drawdown_pct) so we stop adding risk before the backstop.
    """
    state = get_state(session)
    peak = state.peak_equity

    if current_equity > peak:
        update_state(session, peak_equity=current_equity)
        peak = current_equity

    if peak <= 0.0:
        return 1.0

    drawdown_pct = ((peak - current_equity) / peak) * 100.0
    stop_entry = max(12.0, float(settings.flatten_drawdown_pct) - 4.0)  # e.g. 22-4 = 18%

    if drawdown_pct < 5.0:
        return 1.0
    elif drawdown_pct < 10.0:
        return 0.7
    elif drawdown_pct < 14.0:
        return 0.45
    elif drawdown_pct < stop_entry:
        return 0.25
    else:
        return 0.0  # stop opening new risk; backstop flatten handles the rest
