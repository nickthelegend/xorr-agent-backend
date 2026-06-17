"""Smoke test: run one full pipeline scan + one monitor tick in simulation mode
against live Binance data, then print the resulting paper portfolio state."""
import os
import sys
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session

from config import settings
from persistence.db import init_db, engine
from persistence.repo import get_positions, get_trades, get_state
from core import sim_ledger
from core.twak_executor import TwakExecutor
from engine.pipeline import run_pipeline_cycle
from engine.monitor import monitor_tick


async def main():
    init_db()
    sim_ledger.reset(starting_usdt=100.0, starting_bnb=0.05)
    ex = TwakExecutor(settings, simulation=True)

    print("=== PIPELINE CYCLE ===")
    with Session(engine) as s:
        await run_pipeline_cycle(s, ex)

    print("\n=== MONITOR TICK ===")
    with Session(engine) as s:
        await monitor_tick(s, ex)

    with Session(engine) as s:
        positions = get_positions(s)
        trades = get_trades(s)
        cash = sim_ledger.get_cash()
        print(f"\nCash (USDT): ${cash:.2f}")
        print(f"Open positions: {len(positions)}")
        for p in positions:
            print(f"  {p.symbol} size={p.size:.6f} entry=${p.entry_price:.6f} invested=${p.invested:.2f} strat={p.strategy}")
        print(f"Trades recorded: {len(trades)}")
        for t in trades[:10]:
            print(f"  {t.symbol} status={t.status} invested=${t.invested:.2f} pnl=${t.pnl_usd:.4f} ({t.pnl_pct:.2f}%) reason={t.exit_reason}")


if __name__ == "__main__":
    asyncio.run(main())
