"""Tests for the broader trader.dev spot ports (StochRSI MR / ADX trend / BB breakout)."""
import math
import pytest
from datetime import datetime, timezone

from core.types import Candle, MarketContext
from strategies.spot_ports import (
    stochrsi_series, adx_dmi, StochRsiMrPerp, AdxTrendPerp, BbBreakoutPerp, IDEAS,
)

CTX_UP = MarketContext(timestamp=datetime.now(timezone.utc), fear_greed_value=50,
    fear_greed_label="N", btc_dominance=55.0, total_market_cap_usd=2.5e12,
    total_market_cap_change_24h=1.0, bnb_price_usd=600.0, regime="TREND_UP", confluence=80.0)


def _candles(closes):
    return [Candle(ts=datetime.now(timezone.utc), open=c, high=c * 1.01, low=c * 0.99,
                   close=c, volume=1000.0) for c in closes]


def test_stochrsi_series_bounds_and_extremes():
    base = [100.0 + 5.0 * math.sin(i / 4.0) for i in range(80)]
    s = stochrsi_series(base)
    assert len(s) > 0 and all(0.0 <= x <= 1.0 for x in s)
    # a sharp up-spike makes the latest RSI the highest in its window -> StochRSI ~1;
    # a sharp flush makes it the lowest -> ~0.
    up = stochrsi_series(base + [base[-1] * 1.5])
    dn = stochrsi_series(base + [base[-1] * 0.5])
    assert up[-1] > 0.8 and dn[-1] < 0.2


def test_adx_dmi_directional_on_trend():
    up = adx_dmi(_candles([100.0 + i for i in range(80)]), 14)
    assert up and up["pdi"] > up["mdi"] and up["adx"] > 0
    dn = adx_dmi(_candles([200.0 - i for i in range(80)]), 14)
    assert dn and dn["mdi"] > dn["pdi"]


def test_stochrsi_series_empty_when_short():
    assert stochrsi_series([100.0] * 10) == []


@pytest.mark.anyio
async def test_stochrsi_fires_long_on_oversold_crossunder():
    # long uptrend (StochRSI pinned high) then one sharp flush -> latest RSI is the
    # lowest in its window -> StochRSI crosses under 0.2 -> mean-reversion LONG.
    closes = [100.0 + i for i in range(80)] + [80.0]
    sig = await StochRsiMrPerp().evaluate("ETH", [], _candles(closes), CTX_UP)
    assert sig is not None
    assert sig.strategy_name == "stochrsi_mr_perp"
    assert sig.direction == "long" and sig.venue == "perp" and sig.symbol == "ETH"


@pytest.mark.anyio
async def test_non_perp_symbol_refused():
    assert await StochRsiMrPerp().evaluate("CAKE", [], _candles([100.0] * 80), CTX_UP) is None


@pytest.mark.anyio
@pytest.mark.parametrize("cls", [StochRsiMrPerp, AdxTrendPerp, BbBreakoutPerp])
async def test_evaluate_never_raises_wellformed(cls):
    for closes in ([100.0 - i * 0.3 for i in range(120)],
                   [60.0 + i * 0.3 for i in range(120)],
                   [100.0] * 120):
        sig = await cls().evaluate("ETH", [], _candles(closes), CTX_UP)
        if sig is not None:
            assert sig.venue == "perp" and sig.symbol == "ETH"
            assert sig.direction in ("long", "short") and sig.leverage > 0


def test_ideas_exposes_all_three():
    assert set(IDEAS) == {"stochrsi_mr_perp", "adx_trend_perp", "bb_breakout_perp"}
