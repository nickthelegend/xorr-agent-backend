"""Clear all trades/positions/stats and reset the paper ledger for a fresh simulation.

Use before a clean simulation run (and as the pre-flight reset before June 22 live).

  python -m scripts.reset_sim            # reset to SIM_START_USDT (config default)
  python -m scripts.reset_sim --usdt 42  # explicit starting paper cash
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, delete, select  # noqa: E402
from persistence.db import engine, init_db  # noqa: E402
from persistence.models import Trade, Position  # noqa: E402
from persistence.repo import get_state  # noqa: E402
from core import sim_ledger  # noqa: E402
from config import settings  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Reset the paper-trading state")
    ap.add_argument("--usdt", type=float, default=None, help="starting paper USDT (default: SIM_START_USDT)")
    args = ap.parse_args()

    init_db()
    start = args.usdt if args.usdt is not None else float(settings.sim_start_usdt)

    with Session(engine) as s:
        n_trades = len(s.exec(select(Trade)).all())
        n_pos = len(s.exec(select(Position)).all())
        s.exec(delete(Trade))
        s.exec(delete(Position))
        # StrategyStat is optional (rolling R per strategy); clear it for a clean slate.
        try:
            from persistence.models import StrategyStat
            s.exec(delete(StrategyStat))
        except Exception:
            pass
        s.commit()
        st = get_state(s)
        st.mode = "simulation"
        st.scheduler_state = "IDLE"
        st.risk_paused_until = 0.0
        st.peak_equity = 0.0  # CRITICAL: clear stale peak so the kill switch re-baselines to actual start (else a lower reset reads as a false drawdown)
        s.add(st)
        s.commit()

    sim_ledger.reset(start)
    print(f"Cleared {n_trades} trades + {n_pos} positions.")
    print(f"Paper ledger reset to {start:.2f} USDT. mode=simulation, scheduler=IDLE.")


if __name__ == "__main__":
    main()
