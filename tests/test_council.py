import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

from core.types import Signal, MarketContext
from brain.council import score_council, deterministic_fallback
from config import settings

@pytest.fixture
def mock_signals():
    return [
        Signal(
            symbol="CAKE",
            contract="0x123",
            side="buy",
            confidence=0.7,
            stop_loss_pct=1.5,
            take_profit_pct=3.0,
            max_hold_min=120,
            rationale="Test rationale",
            strategy_name="mean_reversion"
        )
    ]

@pytest.fixture
def mock_context():
    return MarketContext(
        timestamp=datetime.now(timezone.utc),
        fear_greed_value=50,
        fear_greed_label="Neutral",
        btc_dominance=55.0,
        total_market_cap_usd=2.5e12,
        total_market_cap_change_24h=1.5,
        bnb_price_usd=600.0,
        regime="TREND_UP",
        confluence=80.0
    )

def create_mock_completion(content: str):
    mock_choice = MagicMock()
    mock_choice.message.content = content
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    return mock_completion

@pytest.mark.anyio
@patch("brain.council.AsyncGroq")
async def test_council_happy_path(mock_groq_class, mock_signals, mock_context):
    # Setup mocks
    mock_client = MagicMock()
    mock_groq_class.return_value = mock_client
    
    # 3 mock responses corresponding to the 3 models
    c1 = create_mock_completion('{"scores": [{"symbol": "CAKE", "score": 0.8, "reasoning": "Primary ok", "red_flags": []}]}')
    c2 = create_mock_completion('{"scores": [{"symbol": "CAKE", "score": 0.9, "reasoning": "Verifier ok", "red_flags": []}]}')
    c3 = create_mock_completion('{"scores": [{"symbol": "CAKE", "score": 0.7, "reasoning": "Fast ok", "red_flags": []}]}')
    
    mock_client.chat.completions.create = AsyncMock(side_effect=[c1, c2, c3])
    
    # Temporarily set key so mock is used
    with patch.object(settings, "groq_api_key", "test_key"):
        decisions = await score_council(mock_signals, mock_context)
        
        assert len(decisions) == 1
        d = decisions[0]
        assert d.symbol == "CAKE"
        # Weighted score: 0.8 * 0.45 + 0.9 * 0.35 + 0.7 * 0.2 = 0.36 + 0.315 + 0.14 = 0.815
        assert abs(d.council_score - 0.815) < 1e-5
        assert len(d.votes) == 3

@pytest.mark.anyio
@patch("brain.council.AsyncGroq")
async def test_council_disagreement(mock_groq_class, mock_signals, mock_context):
    mock_client = MagicMock()
    mock_groq_class.return_value = mock_client
    
    # High disagreement: scores are 0.9, 0.1, 0.5
    c1 = create_mock_completion('{"scores": [{"symbol": "CAKE", "score": 0.9, "reasoning": "Bullish", "red_flags": []}]}')
    c2 = create_mock_completion('{"scores": [{"symbol": "CAKE", "score": 0.1, "reasoning": "Adversarial", "red_flags": ["opposing_whale_flow"]}]}')
    c3 = create_mock_completion('{"scores": [{"symbol": "CAKE", "score": 0.5, "reasoning": "Neutral", "red_flags": []}]}')
    
    mock_client.chat.completions.create = AsyncMock(side_effect=[c1, c2, c3])
    
    with patch.object(settings, "groq_api_key", "test_key"):
        decisions = await score_council(mock_signals, mock_context)
        
        assert len(decisions) == 1
        d = decisions[0]
        # Stddev: mean is 0.5. stddev is ((0.4^2 + 0.4^2 + 0^2)/3)^0.5 = (0.32/3)^0.5 = 0.3266
        # Consensus stddev is non-zero
        assert d.consensus > 0.3
        # Penalty should cap final confidence
        assert d.final_confidence < d.council_score

@pytest.mark.anyio
@patch("brain.council.AsyncGroq")
async def test_council_model_error_redistribution(mock_groq_class, mock_signals, mock_context):
    mock_client = MagicMock()
    mock_groq_class.return_value = mock_client
    
    # Model 1 succeeds, Model 2 fails, Model 3 succeeds
    c1 = create_mock_completion('{"scores": [{"symbol": "CAKE", "score": 0.8, "reasoning": "Primary ok", "red_flags": []}]}')
    c3 = create_mock_completion('{"scores": [{"symbol": "CAKE", "score": 0.7, "reasoning": "Fast ok", "red_flags": []}]}')
    
    # Create side effects: success, error exception, success
    mock_client.chat.completions.create = AsyncMock(side_effect=[c1, Exception("Groq Timeout"), c3])
    
    with patch.object(settings, "groq_api_key", "test_key"):
        decisions = await score_council(mock_signals, mock_context)
        
        assert len(decisions) == 1
        d = decisions[0]
        # Only 2 votes recorded
        assert len(d.votes) == 2
        # Weights: primary (0.45) and fast (0.20). Sum is 0.65.
        # Normalized weights: primary = 0.45/0.65 = 0.6923, fast = 0.20/0.65 = 0.3077
        # Score = 0.8 * (0.45/0.65) + 0.7 * (0.2/0.65) = 0.7692
        assert abs(d.council_score - 0.7692) < 1e-3

@pytest.mark.anyio
@patch("brain.council.AsyncGroq")
async def test_council_all_fail_fallback(mock_groq_class, mock_signals, mock_context):
    mock_client = MagicMock()
    mock_groq_class.return_value = mock_client
    
    # All 3 models throw exception
    mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API Outage"))
    
    with patch.object(settings, "groq_api_key", "test_key"):
        decisions = await score_council(mock_signals, mock_context)
        
        assert len(decisions) == 1
        d = decisions[0]
        # Fallback blends market confluence with the signal's own conviction:
        # 0.5*(80/100) + 0.5*0.7 = 0.75
        assert abs(d.council_score - 0.75) < 1e-9
        assert abs(d.final_confidence - 0.75) < 1e-9
        assert d.votes[0]["model"] == "Deterministic Fallback"
