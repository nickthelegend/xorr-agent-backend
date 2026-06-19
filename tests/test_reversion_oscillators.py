"""Tests for the new reversion oscillators (CCI / Williams %R / BB-bounce / MFI)."""
import math
import pytest
from datetime import datetime, timezone

from core.types import Candle, MarketContext
from strategies.reversion_oscillators import (
    cci_last2, williams_last2, mfi_last2,
    CciMrPerp, WilliamsMrPerp, BbBouncePerp, MfiMrPerp, IDEAS,
)

CTX = MarketContext(timestamp=datetime.now(timezone.utc), fear_greed_value=50,
    fear_greed_label="N", btc_dominance=55.0, total_market_cap_usd=2.5e12,
    total_market_cap_change_24h=1.0, bnb_price_usd=600.0, regime="TREND_UP", confluence=80.0)


def _candles(closes, vols=None):
    return [Candle(ts=datetime.now(timezone.utc), open=c, high=c * 1.01, low=c * 0.99,
                   close=c, volume=(vols[i] if vols else 1000.0)) for i, c in enumerate(closes)]


def test_indicator_ranges():
    cs = _candles([100.0 + 6.0 * math.sin(i / 5.0) for i in range(60)])
    cci = cci_last2(cs, 20); wr = williams_last2(cs, 14); mfi = mfi_last2(cs, 14)
    assert cci and all(math.isfinite(x) for x in cci)
    assert wr and all(-100.0 <= x <= 0.0 for x in wr)
    assert mfi and all(0.0 <= x <= 100.0 for x in mfi)


def test_indicators_none_when_short():
    assert cci_last2(_candles([100.0] * 10)) is None
    assert williams_last2(_candles([100.0] * 10)) is None
    assert mfi_last2(_candles([100.0] * 10)) is None


@pytest.mark.anyio
async def test_cci_long_fires_on_sharp_flush():
    # flat, then one sharp down bar -> CCI crosses under -100 -> mean-reversion long
    closes = [100.0] * 45 + [92.0]
    prev, cur = cci_last2(_candles(closes), 20)
    assert prev >= -100.0 and cur < -100.0
    sig = await CciMrPerp().evaluate("ETH", [], _candles(closes), CTX)
    assert sig is not None and sig.direction == "long" and sig.venue == "perp" and sig.symbol == "ETH"


@pytest.mark.anyio
async def test_williams_long_fires_on_sharp_flush():
    closes = [100.0] * 30 + [92.0]
    sig = await WilliamsMrPerp().evaluate("ETH", [], _candles(closes), CTX)
    assert sig is not None and sig.direction == "long"


@pytest.mark.anyio
async def test_non_perp_symbol_refused():
    assert await CciMrPerp().evaluate("CAKE", [], _candles([100.0] * 46), CTX) is None


@pytest.mark.anyio
@pytest.mark.parametrize("cls", [CciMrPerp, WilliamsMrPerp, BbBouncePerp, MfiMrPerp])
async def test_evaluate_never_raises_wellformed(cls):
    for closes in ([100.0 - i * 0.3 for i in range(60)],
                   [70.0 + i * 0.3 for i in range(60)],
                   [100.0] * 60):
        sig = await cls().evaluate("ETH", [], _candles(closes), CTX)
        if sig is not None:
            assert sig.venue == "perp" and sig.symbol == "ETH" and sig.direction in ("long", "short")


def test_ideas_exposes_all_four():
    assert set(IDEAS) == {"cci_mr_perp", "williams_mr_perp", "bb_bounce_perp", "mfi_mr_perp"}
