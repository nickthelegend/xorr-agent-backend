"""Run a few pipeline cycles synchronously in simulation and report whether the
agent opens risk-managed paper positions (verifies the active-sim config)."""
import os, sys, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from sqlmodel import Session, select
from config import settings
from persistence.db import init_db, engine
from persistence.models import Position, Trade
from core import sim_ledger
from core.twak_executor import TwakExecutor
from engine.pipeline import run_pipeline_cycle
from engine.monitor import monitor_tick


async def main():
    init_db()
    ex = TwakExecutor(settings, simulation=True)
    print(f"sim_council_min={settings.sim_council_min} max_pos={settings.max_concurrent_positions}")
    for i in range(5):
        with Session(engine) as s:
            await run_pipeline_cycle(s, ex)
            await monitor_tick(s, ex)
            pos = s.exec(select(Position)).all()
        print(f"cycle {i+1}: open positions = {len(pos)}  cash=${sim_ledger.get_cash():.2f}")
    from persistence.models import EngineLog
    with Session(engine) as s:
        pos = s.exec(select(Position)).all()
        trades = s.exec(select(Trade)).all()
        print("\n=== recent engine decisions ===")
        for l in reversed(s.exec(select(EngineLog).order_by(EngineLog.id.desc()).limit(14)).all()):
            print(f"  {l.msg[:110]}")
    print("\n=== FINAL ===")
    print("open positions:", len(pos))
    for p in pos:
        print(f"  {p.symbol:<6} entry=${p.entry_price:.6f} size={p.size:.4f} inv=${p.invested:.2f} SL=${p.stop_loss:.6f} TP=${p.take_profit:.6f} strat={p.strategy}")
    print("total trades:", len(trades))


if __name__ == "__main__":
    asyncio.run(main())
