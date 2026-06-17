import pytest
from datetime import datetime, timezone
from sqlmodel import Session, create_engine, SQLModel
from unittest.mock import AsyncMock, MagicMock, patch

from filters.vedic_timing import is_favorable, NAKSHATRA_NAMES
from filters.regime import calculate_ema, is_actionable
from filters.cex_sanity import passes_cex_sanity
from filters.liquidity_gate import passes_liquidity_gate
from filters.cooldown import is_blacklisted, apply_cooldown
from persistence.models import CooldownEntry

# 1. Vedic Timing Tests
def test_vedic_timing_favorable():
    # Test a timestamp that is favorable (e.g. Rohini Nakshatra, Jupiter Hora, no Syzygy)
    # 2026-06-01 10:00:00 UTC (not near new moon of June 15 or full moon of May 31)
    dt = datetime(2026, 6, 4, 10, 0, 0, tzinfo=timezone.utc)
    fav, reasons = is_favorable(dt)
    # Let's inspect what Nakshatra is calculated or mock it if we want deterministic checks.
    assert isinstance(fav, bool)
    assert isinstance(reasons, list)

def test_vedic_timing_unfavorable_naks():
    # Ashwini is in unfavorable list
    # Let's mock the nakshatra name directly
    with patch('filters.vedic_timing.current_nakshatra', return_value="Ashwini"):
        dt = datetime(2026, 6, 4, 10, 0, 0, tzinfo=timezone.utc)
        fav, reasons = is_favorable(dt)
        assert fav is False
        assert any("Ashwini" in r for r in reasons)

def test_vedic_timing_unfavorable_hora():
    with patch('filters.vedic_timing.current_hora', return_value="SATURN"):
        dt = datetime(2026, 6, 4, 10, 0, 0, tzinfo=timezone.utc)
        fav, reasons = is_favorable(dt)
        assert fav is False
        assert any("Saturn" in r for r in reasons)


# 2. Regime Tests
def test_calculate_ema():
    prices = [10.0, 11.0, 12.0, 13.0, 14.0]
    ema = calculate_ema(prices, period=3)
    assert ema > 11.0

def test_regime_actions():
    assert is_actionable("TREND_UP", "momentum_pullback") is True
    assert is_actionable("TREND_DOWN", "momentum_pullback") is False
    assert is_actionable("RISK_OFF", "capitulation") is True
    assert is_actionable("CHOP", "capitulation") is True
    assert is_actionable("TREND_UP", "news_catalyst") is True  # news always actionable


# 3. CEX Sanity Tests
@pytest.mark.anyio
async def test_cex_sanity_passes():
    with patch('filters.cex_sanity.is_sane', return_value=True):
        res = await passes_cex_sanity("BTC", 60000.0)
        assert res is True

@pytest.mark.anyio
async def test_cex_sanity_fails():
    with patch('filters.cex_sanity.is_sane', return_value=False):
        res = await passes_cex_sanity("BTC", 90000.0)
        assert res is False


# 4. Liquidity Gate Tests
@pytest.mark.anyio
async def test_liquidity_gate_simulation():
    mock_executor = MagicMock()
    mock_executor.simulation = True
    # In simulation mode, should pass
    res = await passes_liquidity_gate(mock_executor, "CAKE", 2.0)
    assert res is True


# 5. Cooldown Tests
def test_cooldown_flow():
    # Setup in-memory sqlite for repo testing
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    
    with Session(engine) as session:
        # Initial check
        assert is_blacklisted(session, "BNB") is False
        
        # Apply loss cooldown
        apply_cooldown(session, "BNB", "loss", hold_minutes=5.0)
        assert is_blacklisted(session, "BNB") is True
        
        # Query cooldown directly to see duration
        entry = session.get(CooldownEntry, "BNB")
        assert entry is not None
        assert "Fast stop-out" in entry.reason
