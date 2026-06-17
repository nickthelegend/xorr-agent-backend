import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from core.types import MarketContext, Quote, Candle
from filters.confluence_score import confluence_score, passes as confluence_passes
from filters.volume_gate import passes as volume_passes
from filters.whale_netflow import is_bullish as whale_is_bullish

def create_mock_candle(close: float, high: float = None, low: float = None, volume: float = 100.0):
    return Candle(
        ts=datetime.now(timezone.utc),
        open=close,
        high=high or close * 1.01,
        low=low or close * 0.99,
        close=close,
        volume=volume
    )

@pytest.fixture
def mock_context():
    quotes = {
        "CAKE": Quote(symbol="CAKE", price=2.0, pct_1h=1.0, pct_24h=5.0, volume_24h=1000000.0, market_cap=200000000.0, last_updated=datetime.now(timezone.utc)),
        "BNB": Quote(symbol="BNB", price=600.0, pct_1h=-0.5, pct_24h=2.0, volume_24h=10000000.0, market_cap=90000000000.0, last_updated=datetime.now(timezone.utc)),
        "LOWVOL": Quote(symbol="LOWVOL", price=1.0, pct_1h=0.0, pct_24h=0.0, volume_24h=10000.0, market_cap=1000000.0, last_updated=datetime.now(timezone.utc))
    }
    return MarketContext(
        timestamp=datetime.now(timezone.utc),
        fear_greed_value=50,
        fear_greed_label="Neutral",
        btc_dominance=55.0,
        total_market_cap_usd=2.5e12,
        total_market_cap_change_24h=1.5,
        bnb_price_usd=600.0,
        quotes=quotes,
        open_positions=[],
        regime="TREND_UP",
        confluence=0.0
    )

@pytest.mark.anyio
@patch("filters.confluence_score.fetch_binance_klines")
async def test_confluence_score(mock_fetch, mock_context):
    # Setup mock candles
    # Technical strength: RSI around 50 (in range), price above 20 EMA, MACD positive
    candles_5m = [create_mock_candle(close=float(x)) for x in range(10, 45)] # 35 candles
    candles_1h = [create_mock_candle(close=float(x)) for x in range(10, 40)] # 30 candles
    
    mock_fetch.side_effect = lambda sym, interval, limit: candles_5m if interval == "5m" else candles_1h
    
    score, breakdown = await confluence_score(mock_context, "CAKE")
    assert isinstance(score, int)
    assert score > 0
    assert "momentum" in breakdown
    assert "technical" in breakdown
    assert "range_position" in breakdown

@pytest.mark.anyio
@patch("filters.volume_gate.fetch_binance_klines")
async def test_volume_gate(mock_fetch, mock_context):
    # High-volume token
    candles_5m = [create_mock_candle(close=2.0, volume=100.0) for _ in range(25)]
    mock_fetch.return_value = candles_5m
    
    assert await volume_passes(mock_context, "CAKE") is True
    
    # Low-volume token (volume_24h < 500k)
    assert await volume_passes(mock_context, "LOWVOL") is False

@pytest.mark.anyio
@patch("filters.whale_netflow.get_cached_mcp_skill")
@patch("filters.whale_netflow.fetch_binance_klines")
async def test_whale_netflow(mock_fetch, mock_mcp, mock_context):
    # 1. Positive net flow -> bullish
    mock_mcp.return_value = {"net_flow": 50000.0}
    assert await whale_is_bullish(mock_context, "CAKE") is True
    
    # 2. Extreme negative net flow (<= -5% of 24h volume) -> rejected
    # CAKE 24h volume is 1,000,000. -5% is -50,000. Let's return -60,000.
    mock_mcp.return_value = {"net_flow": -60000.0}
    assert await whale_is_bullish(mock_context, "CAKE") is False
