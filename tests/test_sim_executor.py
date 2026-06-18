"""End-to-end checks for the simulation paper-trading executor + ledger.

Guards against the original bug where simulated entries stored an inverted
price and exits (min_out=0) always realized a ~100% loss.
"""
import pytest
from decimal import Decimal

from config import settings
from persistence.db import init_db
from core import sim_ledger
from core.twak_executor import TwakExecutor, SIM_SWAP_FEE, SIM_REALIZED_SLIPPAGE

USDT = settings.usdt_contract
TOKEN = "0x000000000000000000000000000000000000dEaD"  # arbitrary non-USDT contract


@pytest.fixture(autouse=True)
def _db():
    init_db()
    sim_ledger.reset(starting_usdt=100.0, starting_bnb=0.05)


@pytest.mark.anyio
async def test_buy_fills_at_real_price_and_debits_cash():
    ex = TwakExecutor(settings, simulation=True)
    cost = SIM_SWAP_FEE + SIM_REALIZED_SLIPPAGE

    res = await ex.swap(USDT, TOKEN, Decimal("10"), Decimal("0"), "ENTRY_test", ref_price=2.0)

    assert res.success
    # executed_price must be the true USD price, not its inverse
    assert res.executed_price == pytest.approx(2.0)
    # tokens received = usable_usd / price, after fee+slippage
    assert res.amount_out == pytest.approx(10.0 * (1.0 - cost) / 2.0)
    # cash debited by the full notional
    assert float(await ex.get_balance("USDT")) == pytest.approx(90.0)


@pytest.mark.anyio
async def test_round_trip_profit_when_price_rises():
    ex = TwakExecutor(settings, simulation=True)
    cost = SIM_SWAP_FEE + SIM_REALIZED_SLIPPAGE

    buy = await ex.swap(USDT, TOKEN, Decimal("10"), Decimal("0"), "ENTRY_test", ref_price=2.0)
    sell = await ex.swap(TOKEN, USDT, Decimal(str(buy.amount_out)), Decimal("0"), "EXIT_test", ref_price=2.2)

    expected_usd = buy.amount_out * 2.2 * (1.0 - cost)
    assert sell.amount_out == pytest.approx(expected_usd)
    # +10% move beats ~3.5% round-trip costs -> net profit, and clearly not a -100% wipeout
    pnl = sell.amount_out - 10.0
    assert pnl > 0
    # cash restored to ~ starting minus the small round-trip cost plus the gain
    assert float(await ex.get_balance("USDT")) == pytest.approx(90.0 + sell.amount_out)


@pytest.mark.anyio
async def test_round_trip_loss_is_bounded_not_total():
    ex = TwakExecutor(settings, simulation=True)
    buy = await ex.swap(USDT, TOKEN, Decimal("10"), Decimal("0"), "ENTRY_test", ref_price=2.0)
    # price falls 5%
    sell = await ex.swap(TOKEN, USDT, Decimal(str(buy.amount_out)), Decimal("0"), "EXIT_test", ref_price=1.9)
    pnl = sell.amount_out - 10.0
    assert pnl < 0          # it's a loss
    assert pnl > -2.0       # but bounded (~ -5% move + costs), nowhere near -100%


@pytest.mark.anyio
async def test_swap_without_reference_price_fails_safely():
    ex = TwakExecutor(settings, simulation=True)
    res = await ex.swap(USDT, TOKEN, Decimal("10"), Decimal("0"), "ENTRY_test", ref_price=0.0)
    assert not res.success
    # cash untouched on a failed fill
    assert float(await ex.get_balance("USDT")) == pytest.approx(100.0)
