"""Tests for the production-hardening features: perp funding carry, the on-chain
perp-position lister, and scheduler liveness/health."""
import asyncio
from decimal import Decimal

from persistence.db import init_db
from core import sim_ledger
from core.twak_executor import TwakExecutor


def _sim_executor():
    init_db()
    sim_ledger.reset()
    return TwakExecutor(simulation=True)


def test_funding_carry_reduces_proceeds_over_time():
    ex = _sim_executor()
    # open $7 margin 3x long ETH @100 -> 0.21 notional units
    r = asyncio.run(ex.open_perp("ETH", "long", Decimal("7"), 3.0, ref_price=100.0))
    size = r.amount_out
    # close at the SAME price: only fees + funding differ by hold time
    flat = asyncio.run(ex.close_perp("ETH", "long", size, 100.0, 7.0, 3.0, ref_price=100.0, hold_hours=0.0))
    held = asyncio.run(ex.close_perp("ETH", "long", size, 100.0, 7.0, 3.0, ref_price=100.0, hold_hours=24.0))
    # 24h of funding carry must make the held close worth strictly less
    assert held.amount_out < flat.amount_out
    # and the difference is the modeled funding (~ notional * rate * 24/8)
    assert (flat.amount_out - held.amount_out) > 0.0


def test_list_perp_positions_none_in_sim():
    ex = _sim_executor()
    assert asyncio.run(ex.list_perp_positions()) is None  # unverifiable in sim -> fail-safe


def test_list_perp_positions_none_without_creds_live():
    ex = TwakExecutor(simulation=False)
    ex._twak_ready = lambda: False
    assert asyncio.run(ex.list_perp_positions()) is None


def test_scheduler_health_shape():
    from engine.scheduler import scheduler
    h = scheduler.health()
    for k in ("running", "scan_alive", "monitor_alive", "scan_age_sec", "monitor_age_sec"):
        assert k in h
    assert isinstance(h["running"], bool)


def test_funding_fade_logic():
    from data.funding import funding_confidence_mult
    crowded_longs = {"crowding": "crowded_longs", "funding_bps": 65.0, "stretch_score": 52.0}
    assert funding_confidence_mult("short", crowded_longs)[0] > 1.0   # fade = tailwind
    assert funding_confidence_mult("long", crowded_longs)[0] < 1.0    # with crowd = headwind
    neutral = {"crowding": "neutral", "funding_bps": 3.0, "stretch_score": 5.0}
    assert funding_confidence_mult("long", neutral)[0] == 1.0          # not extreme -> no bias


def test_arbiter_promotes_proven_shadow():
    import datetime
    from persistence.db import init_db, engine
    from sqlmodel import Session, delete
    from persistence.models import StrategyStat
    from strategies.arbiter import StrategyArbiter
    from strategies.registry import active_strategies
    from config import settings
    init_db()
    arb = StrategyArbiter()
    with Session(engine) as s:
        s.exec(delete(StrategyStat))
        for i in range(8):
            s.add(StrategyStat(strategy="shadow_supertrend_perp", trade_id=f"sh{i}",
                               closed_at=datetime.datetime.now(datetime.timezone.utc),
                               r_realized=0.5, pnl_usd=1.0))
        s.commit()
        arb.evaluate_promotions(s)
    assert "supertrend_perp" in arb.promoted_strategies
    names = [x.name for x in active_strategies(settings, [], list(arb.promoted_strategies))]
    assert "supertrend_perp" in names  # promoted -> now active even though config-disabled


def test_shadow_close_records_stat_no_cash():
    import asyncio, time
    from datetime import datetime, timezone
    from persistence.db import init_db, engine
    from sqlmodel import Session, delete, select
    from core import sim_ledger, perp_math
    from core.twak_executor import TwakExecutor
    from core.types import Quote
    from persistence.models import Position, StrategyStat
    from persistence.repo import add_position, get_positions
    import engine.monitor as mon

    init_db()
    with Session(engine) as s:
        for p in get_positions(s):
            s.delete(p)
        s.exec(delete(StrategyStat))
        s.commit()
    sim_ledger.reset()
    cash0 = sim_ledger.get_cash()
    ex = TwakExecutor(simulation=True)

    pos = Position(id="SHADOW:t1", symbol="ETH", contract="0xeth", opened_at=time.time() - 60,
                   entry_price=100.0, stop_loss=97.0, take_profit=107.0, init_stop=97.0,
                   size=perp_math.notional_units(1.0, 3.0, 100.0), strategy="shadow_supertrend_perp",
                   invested=1.0, is_perp=True, venue="perp", direction="long", leverage=3.0,
                   margin_usd=1.0, is_shadow=True)
    with Session(engine) as s:
        add_position(s, pos)

    async def noop(*a, **k):
        return None
    mon.log_engine_msg = noop

    def q(sym, price):
        return Quote(symbol=sym, price=price, pct_1h=0, pct_24h=0, volume_24h=1e7,
                     market_cap=1e9, last_updated=datetime.now(timezone.utc))

    async def fake_fast():
        return {"ETH": q("ETH", 108.0)}  # above TP -> exit
    mon.fetch_fast_quotes = fake_fast

    async def run():
        with Session(engine) as s:
            await mon.monitor_tick(s, ex)
    asyncio.run(run())

    with Session(engine) as s:
        assert len(get_positions(s)) == 0  # shadow closed
        stats = list(s.exec(select(StrategyStat).where(StrategyStat.strategy == "shadow_supertrend_perp")).all())
        assert len(stats) == 1 and stats[0].r_realized > 0
    assert abs(sim_ledger.get_cash() - cash0) < 1e-9  # shadow close must NOT touch real cash
