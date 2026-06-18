"""Tests for the trader.dev-ported oscillator mean-reversion strategies
(tsi_mr_perp / uo_mr_perp / aroon_mr_perp).

Aroon's oscillator is a pure windowed function (no EMA state), so we engineer an
exact crossunder of -entryThresh and assert the long fade fires. TSI/UO use
double-EMA smoothing, so we assert their series are finite + correct length and
that evaluate() never raises and only ever returns a well-formed perp signal.
"""
import math
import pytest
from datetime import datetime, timezone

from core.types import Candle, MarketContext
from strategies.oscillator_mr import (
    tsi_series, uo_series, aroon_osc_last2,
    TsiMrPerp, UoMrPerp, AroonMrPerp, IDEAS,
)

CTX = MarketContext(
    timestamp=datetime.now(timezone.utc), fear_greed_value=50,
    fear_greed_label="Neutral", btc_dominance=55.0, total_market_cap_usd=2.5e12,
    total_market_cap_change_24h=1.5, bnb_price_usd=600.0, regime="TREND_UP",
    confluence=80.0,
)


def _candles(closes, highs=None, lows=None):
    out = []
    for i, c in enumerate(closes):
        out.append(Candle(
            ts=datetime.now(timezone.utc), open=c,
            high=highs[i] if highs else c * 1.01,
            low=lows[i] if lows else c * 0.99,
            close=c, volume=1000.0,
        ))
    return out


def test_indicator_series_are_finite_and_sized():
    # a noisy-but-bounded series; all three indicators should produce finite output
    closes = [100.0 + 5.0 * math.sin(i / 3.0) for i in range(80)]
    candles = _candles(closes)

    t = tsi_series(closes)
    assert len(t) > 0 and all(math.isfinite(x) and -100.0 <= x <= 100.0 for x in t)

    u = uo_series(candles)
    assert len(u) > 0 and all(math.isfinite(x) and 0.0 <= x <= 100.0 for x in u)

    a = aroon_osc_last2(candles, 25)
    assert a is not None and all(math.isfinite(x) and -100.0 <= x <= 100.0 for x in a)


def test_indicator_series_empty_when_too_short():
    short = [100.0] * 10
    assert tsi_series(short) == []
    assert uo_series(_candles(short)) == []
    assert aroon_osc_last2(_candles(short), 25) is None


@pytest.mark.anyio
async def test_aroon_long_fires_on_engineered_crossunder():
    # 30 bars, Aroon period 25, entry thresh 50 (osc = 4*(hh-ll) for period 25).
    # Unique highest high at bar 16, lowest low at bar 29, 2nd-lowest at bar 28.
    #   cur window candles[5:30]:  hh@local 11, ll@local 24 -> osc = 4*(11-24) = -52  (< -50)
    #   prev window candles[4:29]: hh@local 12, ll@local 24 -> osc = 4*(12-24) = -48  (>= -50)
    # => crossunder of -50 -> mean-reversion LONG.
    closes = [100.0] * 30
    highs = [100.0] * 30
    lows = [100.0] * 30
    highs[16] = 110.0   # unique highest high
    lows[29] = 80.0     # unique lowest low (most recent)
    lows[28] = 85.0     # second lowest

    candles = _candles(closes, highs=highs, lows=lows)
    prev, cur = aroon_osc_last2(candles, 25)
    assert prev == -48.0 and cur == -52.0   # exact crossunder of -50

    sig = await AroonMrPerp().evaluate("ETH", [], candles, CTX)
    assert sig is not None
    assert sig.strategy_name == "aroon_mr_perp"
    assert sig.direction == "long" and sig.side == "buy"
    assert sig.venue == "perp" and sig.symbol == "ETH"


@pytest.mark.anyio
async def test_aroon_skips_non_perp_symbol():
    # CAKE is not in perp_symbols -> must refuse regardless of signal shape
    candles = _candles([100.0] * 30)
    assert await AroonMrPerp().evaluate("CAKE", [], candles, CTX) is None


@pytest.mark.anyio
@pytest.mark.parametrize("cls", [TsiMrPerp, UoMrPerp, AroonMrPerp])
async def test_evaluate_never_raises_and_returns_wellformed(cls):
    # downtrend, uptrend, flat — across the lot, evaluate must not raise and any
    # signal it emits must be a well-formed perp signal on the queried symbol.
    series = {
        "down": [100.0 - i * 0.4 for i in range(80)],
        "up": [60.0 + i * 0.4 for i in range(80)],
        "flat": [100.0] * 80,
    }
    for closes in series.values():
        candles = _candles(closes)
        sig = await cls().evaluate("ETH", candles, candles, CTX)
        if sig is not None:
            assert sig.venue == "perp"
            assert sig.symbol == "ETH"
            assert sig.direction in ("long", "short")
            assert sig.leverage and sig.leverage > 0


def test_ideas_exposes_all_three():
    assert set(IDEAS) == {"tsi_mr_perp", "uo_mr_perp", "aroon_mr_perp"}
