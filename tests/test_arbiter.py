import pytest
from datetime import datetime, timezone
from sqlmodel import Session, create_engine, SQLModel, select
from unittest.mock import MagicMock, patch

from core.types import Signal
from persistence.models import Trade, Position, StrategyStat
from strategies.arbiter import StrategyArbiter
from config import settings

@pytest.fixture
def db_session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session

@pytest.fixture
def mock_signals():
    return [
        Signal(
            symbol="CAKE",
            contract="0xCAKE",
            side="buy",
            confidence=0.8,
            stop_loss_pct=2.0,
            take_profit_pct=4.0,
            max_hold_min=120,
            rationale="Rationale",
            strategy_name="momentum_pullback"
        )
    ]

def test_arbiter_concentration_cap(db_session, mock_signals):
    arbiter = StrategyArbiter()
    
    # 3 existing positions on CAKE with $4 each
    positions = [
        Position(id="1", symbol="CAKE", contract="0xCAKE", opened_at=0.0, entry_price=1.0, stop_loss=0.9, take_profit=1.1, size=4.0, strategy="momentum_pullback", invested=4.0),
        Position(id="2", symbol="CAKE", contract="0xCAKE", opened_at=0.0, entry_price=1.0, stop_loss=0.9, take_profit=1.1, size=4.0, strategy="momentum_pullback", invested=4.0),
        Position(id="3", symbol="CAKE", contract="0xCAKE", opened_at=0.0, entry_price=1.0, stop_loss=0.9, take_profit=1.1, size=4.0, strategy="momentum_pullback", invested=4.0),
    ]
    
    # Total risk is 12 + 2 = 14. CAKE risk is 12 + 2 = 14. 14/14 = 100% (exceeds 50% limit)
    with patch.object(settings, "base_trade_size_usd", 2.0):
        filtered = arbiter.filter(db_session, mock_signals, positions)
        assert len(filtered) == 0  # rejected due to concentration cap

def test_arbiter_suspend_and_revive(db_session, mock_signals):
    arbiter = StrategyArbiter()
    
    # 1. Simulate 11 completed losing trades for "momentum_pullback"
    for i in range(11):
        db_session.add(Trade(
            id=f"t_{i}",
            opened_at=datetime.now(timezone.utc).isoformat(),
            closed_at=datetime.now(timezone.utc).isoformat(),
            symbol="CAKE",
            contract="0xCAKE",
            status="loss",
            invested=2.0,
            pnl_usd=-0.04,
            pnl_pct=-2.0,
            hold_minutes=10.0,
            strategy="momentum_pullback",
            window="COMPETITION",
            tx_open="tx",
            score=0.7
        ))
        # Add a negative StrategyStat
        db_session.add(StrategyStat(
            strategy="momentum_pullback",
            trade_id=f"t_{i}",
            closed_at=datetime.now(timezone.utc),
            r_realized=-1.0,
            pnl_usd=-0.04
        ))
    db_session.commit()
    
    # Verify it gets suspended
    # Mock active count to bypass floor (>3 active strats)
    arbiter.update_suspended_states(db_session)
    assert "momentum_pullback" in arbiter.suspended_strategies
    
    # 2. Simulate 5 completed shadow winning trades (expectancy > 0.15R)
    for i in range(5):
        db_session.add(Trade(
            id=f"ts_{i}",
            opened_at=datetime.now(timezone.utc).isoformat(),
            closed_at=datetime.now(timezone.utc).isoformat(),
            symbol="CAKE",
            contract="0xCAKE",
            status="win",
            invested=2.0,
            pnl_usd=0.08,
            pnl_pct=4.0,
            hold_minutes=10.0,
            strategy="shadow_momentum_pullback",
            window="COMPETITION",
            tx_open="tx",
            score=0.7
        ))
        db_session.add(StrategyStat(
            strategy="shadow_momentum_pullback",
            trade_id=f"ts_{i}",
            closed_at=datetime.now(timezone.utc),
            r_realized=2.0,
            pnl_usd=0.08
        ))
    db_session.commit()
    
    # Verify it gets revived
    arbiter.update_suspended_states(db_session)
    assert "momentum_pullback" not in arbiter.suspended_strategies

def test_arbiter_diversity_floor(db_session):
    arbiter = StrategyArbiter()
    
    # Suspend 5 strategies
    arbiter.suspended_strategies = {
        "momentum_pullback",
        "fib_golden_pocket",
        "capitulation",
        "news_catalyst",
        "mean_reversion"
    }
    
    # Attempting to suspend "trend_follow" (making it 6 suspended, leaving only 2 active)
    # 11 losing trades
    for i in range(11):
        db_session.add(StrategyStat(
            strategy="trend_follow",
            trade_id=f"t_{i}",
            closed_at=datetime.now(timezone.utc),
            r_realized=-1.0,
            pnl_usd=-0.04
        ))
        db_session.add(Trade(
            id=f"t_{i}",
            opened_at=datetime.now(timezone.utc).isoformat(),
            closed_at=datetime.now(timezone.utc).isoformat(),
            symbol="CAKE",
            contract="0xCAKE",
            status="loss",
            invested=2.0,
            pnl_usd=-0.04,
            strategy="trend_follow",
            window="COMPETITION",
            tx_open="tx"
        ))
    db_session.commit()
    
    arbiter.update_suspended_states(db_session)
    # Hard guard should protect trend_follow from suspension
    assert "trend_follow" not in arbiter.suspended_strategies
