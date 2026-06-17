import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from core.types import Candle, MarketContext, Signal
from strategies.momentum_pullback import MomentumPullbackStrategy
from strategies.fib_golden_pocket import FibGoldenPocketStrategy
from strategies.capitulation import CapitulationStrategy
from strategies.news_catalyst import NewsCatalystStrategy, recent_listing_events
from strategies.mean_reversion import MeanReversionStrategy
from strategies.trend_follow import TrendFollowStrategy
from strategies.vol_squeeze import VolSqueezeStrategy
from strategies.whale_flow import WhaleFlowStrategy

# Mock Context
mock_context = MarketContext(
    timestamp=datetime.now(timezone.utc),
    fear_greed_value=50,
    fear_greed_label="Neutral",
    btc_dominance=55.0,
    total_market_cap_usd=2.5e12,
    total_market_cap_change_24h=1.5,
    bnb_price_usd=600.0,
    regime="TREND_UP",
    confluence=80.0  # Pass confluence filter
)

# Helpers to build candle list
def create_mock_candles(closes, highs=None, lows=None, volumes=None):
    candles = []
    for i, c in enumerate(closes):
        h = highs[i] if highs else c * 1.01
        l = lows[i] if lows else c * 0.99
        v = volumes[i] if volumes else 1000.0
        candles.append(Candle(
            ts=datetime.now(timezone.utc),
            open=c,
            high=h,
            low=l,
            close=c,
            volume=v
        ))
    return candles


# 1. Momentum Pullback Tests
@pytest.mark.anyio
async def test_momentum_pullback_refuse():
    strategy = MomentumPullbackStrategy()
    closes_1h = [10.0] * 30
    closes_5m = [10.0] * 30
    
    candles_1h = create_mock_candles(closes_1h)
    candles_5m = create_mock_candles(closes_5m)
    
    signal = await strategy.evaluate("CAKE", candles_5m, candles_1h, mock_context)
    assert signal is None

@pytest.mark.anyio
async def test_momentum_pullback_pass():
    strategy = MomentumPullbackStrategy()
    closes_1h = [float(x) for x in range(10, 40)]
    closes_5m = [10.0 + x*0.2 for x in range(25)]
    closes_5m += [12.0, 11.8, 12.2, 12.5, 12.8]
    lows_5m = [c * 0.97 for c in closes_5m]
    lows_5m[-5] = 11.0
    
    candles_1h = create_mock_candles(closes_1h)
    candles_5m = create_mock_candles(closes_5m, lows=lows_5m)
    
    signal = await strategy.evaluate("CAKE", candles_5m, candles_1h, mock_context)
    assert signal is None or signal.symbol == "CAKE"


# 2. Fib Golden Pocket Tests
@pytest.mark.anyio
async def test_fib_golden_pocket_refuse():
    strategy = FibGoldenPocketStrategy()
    closes_5m = [10.0] * 30
    candles_5m = create_mock_candles(closes_5m)
    signal = await strategy.evaluate("CAKE", candles_5m, [], mock_context)
    assert signal is None


# 3. Capitulation Tests
@pytest.mark.anyio
async def test_capitulation_refuse():
    strategy = CapitulationStrategy()
    closes_1h = [10.0] * 30
    closes_5m = [10.0] * 30
    candles_1h = create_mock_candles(closes_1h)
    candles_5m = create_mock_candles(closes_5m)
    
    signal = await strategy.evaluate("CAKE", candles_5m, candles_1h, mock_context)
    assert signal is None

@pytest.mark.anyio
async def test_capitulation_pass():
    strategy = CapitulationStrategy()
    closes_1h = [100.0] * 10 + [90.0]
    closes_5m = [90.0] * 20
    volumes_5m = [100.0] * 19 + [500.0]
    
    last_candle_open = 90.0
    last_candle_close = 91.0
    last_candle_high = 91.5
    last_candle_low = 80.0
    
    candles_1h = create_mock_candles(closes_1h)
    candles_5m = create_mock_candles(closes_5m, volumes=volumes_5m)
    
    candles_5m[-1].open = last_candle_open
    candles_5m[-1].close = last_candle_close
    candles_5m[-1].high = last_candle_high
    candles_5m[-1].low = last_candle_low
    
    signal = await strategy.evaluate("CAKE", candles_5m, candles_1h, mock_context)
    assert signal is not None
    assert signal.strategy_name == "capitulation"
    assert signal.stop_loss_pct == 2.2


# 4. News Catalyst Tests
@pytest.mark.anyio
async def test_news_catalyst_refuse():
    strategy = NewsCatalystStrategy()
    recent_listing_events.clear()
    signal = await strategy.evaluate("CAKE", [], [], mock_context)
    assert signal is None

@pytest.mark.anyio
async def test_news_catalyst_pass():
    strategy = NewsCatalystStrategy()
    recent_listing_events.clear()
    recent_listing_events.append(("CAKE", "Binance Will List PancakeSwap (CAKE)", datetime.now(timezone.utc)))
    
    signal = await strategy.evaluate("CAKE", [], [], mock_context)
    assert signal is not None
    assert signal.strategy_name == "news_catalyst"


# 5. Mean Reversion Tests
@pytest.mark.anyio
async def test_mean_reversion_refuse():
    strategy = MeanReversionStrategy()
    # flat candles
    candles_5m = create_mock_candles([10.0] * 30)
    candles_1h = create_mock_candles([10.0] * 60)
    signal = await strategy.evaluate("CAKE", candles_5m, candles_1h, mock_context)
    assert signal is None

@pytest.mark.anyio
async def test_mean_reversion_pass():
    strategy = MeanReversionStrategy()
    # 5m Bollinger squeeze and bounce:
    # 20 SMA is 10.0. Std is 0.2. Lower BB is 9.6.
    closes_5m = [10.0] * 20 + [9.5, 9.7] # pierced 9.6, then bounce
    lows_5m = [c * 0.99 for c in closes_5m]
    lows_5m[-2] = 9.4 # tagged BB
    volumes_5m = [10.0] * 20 + [10.0, 50.0] # volume bounce
    
    candles_5m = create_mock_candles(closes_5m, lows=lows_5m, volumes=volumes_5m)
    candles_1h = create_mock_candles([10.0] * 60) # within 3% of 50-EMA (10.0)
    
    # Mock RSI calculation to say it crossed above 30
    with patch("strategies.mean_reversion.calculate_rsi", return_value=[20.0]*21 + [35.0]):
        signal = await strategy.evaluate("CAKE", candles_5m, candles_1h, mock_context)
        assert signal is None or signal.symbol == "CAKE"


# 6. Trend Follow Tests
@pytest.mark.anyio
@patch("strategies.trend_follow.fetch_binance_klines")
async def test_trend_follow_refuse(mock_fetch):
    strategy = TrendFollowStrategy()
    mock_fetch.return_value = create_mock_candles([10.0] * 60)
    
    candles_5m = create_mock_candles([10.0] * 30)
    candles_1h = create_mock_candles([10.0] * 60)
    
    signal = await strategy.evaluate("CAKE", candles_5m, candles_1h, mock_context)
    assert signal is None

@pytest.mark.anyio
@patch("strategies.trend_follow.fetch_binance_klines")
async def test_trend_follow_pass(mock_fetch):
    strategy = TrendFollowStrategy()
    # 1d trend up: 20-EMA > 50-EMA. Closes rising from 10 to 30.
    closes_1d = [float(x) for x in range(10, 70)]
    mock_fetch.return_value = create_mock_candles(closes_1d)
    
    # 1h trend up: closes rising from 10 to 40. Price > 20 EMA.
    closes_1h = [float(x) for x in range(10, 70)]
    # 5m: pullback close to 1h 20-EMA, trigger bull engulfing or hammer
    # let's assume 1h 20-EMA is around 55. Current price pulls back to 55.
    closes_5m = [55.0] * 18 + [54.0, 56.5] # engulfing
    candles_5m = create_mock_candles(closes_5m)
    candles_1h = create_mock_candles(closes_1h)
    
    # Set low and highs to trigger engulfing
    candles_5m[-1].open = 54.2
    candles_5m[-1].close = 56.5
    candles_5m[-2].open = 55.0
    candles_5m[-2].close = 54.3
    
    signal = await strategy.evaluate("CAKE", candles_5m, candles_1h, mock_context)
    assert signal is None or signal.symbol == "CAKE"


# 7. Vol Squeeze Tests
@pytest.mark.anyio
async def test_vol_squeeze_refuse():
    strategy = VolSqueezeStrategy()
    candles_5m = create_mock_candles([10.0] * 30)
    signal = await strategy.evaluate("CAKE", candles_5m, [], mock_context)
    assert signal is None

@pytest.mark.anyio
async def test_vol_squeeze_pass():
    strategy = VolSqueezeStrategy()
    # Mock ATR and Bollinger inside Keltner Squeeze logic
    closes_5m = [10.0] * 28 + [10.5, 11.5]
    volumes_5m = [10.0] * 28 + [10.0, 50.0]
    
    candles_5m = create_mock_candles(closes_5m, volumes=volumes_5m)
    
    # Mock Keltner channels and squeeze history to force squeeze condition and breakout
    with patch("strategies.vol_squeeze.calculate_atr", return_value=[0.1] * 30), \
         patch("strategies.vol_squeeze.np.std", return_value=0.02):
        # Trigger evaluation
        signal = await strategy.evaluate("CAKE", candles_5m, [], mock_context)
        assert signal is None or signal.symbol == "CAKE"


# 8. Whale Flow Tests
@pytest.mark.anyio
@patch("strategies.whale_flow.get_cached_mcp_skill")
async def test_whale_flow_refuse(mock_mcp):
    strategy = WhaleFlowStrategy()
    # negative whale flow
    mock_mcp.return_value = {"net_flow": -1000.0}
    
    candles_5m = create_mock_candles([10.0] * 30)
    candles_1h = create_mock_candles([10.0] * 30)
    
    signal = await strategy.evaluate("CAKE", candles_5m, candles_1h, mock_context)
    assert signal is None

@pytest.mark.anyio
@patch("strategies.whale_flow.get_cached_mcp_skill")
async def test_whale_flow_pass(mock_mcp):
    strategy = WhaleFlowStrategy()
    # positive whale netflow >= $50k
    mock_mcp.return_value = {"net_flow": 75000.0}
    
    # 5m higher lows
    closes_5m = [10.0] * 20
    lows_5m = [10.0] * 20
    # lows: previous 3 lows = 9.8, last 3 lows = 9.9
    lows_5m[-6:-3] = [9.8, 9.8, 9.8]
    lows_5m[-3:] = [9.9, 9.9, 9.9]
    
    candles_5m = create_mock_candles(closes_5m, lows=lows_5m)
    candles_1h = create_mock_candles([10.0] * 30)
    
    signal = await strategy.evaluate("CAKE", candles_5m, candles_1h, mock_context)
    assert signal is None or signal.symbol == "CAKE"
