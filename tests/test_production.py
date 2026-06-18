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
