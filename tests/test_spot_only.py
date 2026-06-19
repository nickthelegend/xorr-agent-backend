"""Spot-only competition mode: the agent must never short or use leverage.

Two guarantees are tested:
  1. enforce_spot_only() — the pipeline chokepoint — drops shorts and forces every
     surviving signal to a 1x spot long (perp longs become spot; spot passes through).
  2. executor.open_perp() fails closed whenever settings.spot_only is True, so even a
     stray perp signal can never open a leveraged position.
"""
import pytest
from decimal import Decimal

from core.types import Signal
from engine.pipeline import enforce_spot_only
from core.twak_executor import TwakExecutor
from config import settings


def _sig(direction, venue="perp", leverage=3.0, name="liq_reversion_perp"):
    return Signal(
        symbol="ETH", contract="0xabc", side="buy" if direction == "long" else "sell",
        confidence=0.8, stop_loss_pct=3.0, take_profit_pct=5.0, max_hold_min=480,
        rationale="t", strategy_name=name, direction=direction, venue=venue, leverage=leverage,
    )


def test_enforce_spot_only_drops_shorts_and_delevers_longs():
    sigs = [_sig("long"), _sig("short"), _sig("long", name="aroon_mr_perp")]
    out = enforce_spot_only(sigs)
    assert len(out) == 2                                  # the short is dropped
    assert all(s.direction == "long" for s in out)
    assert all(s.venue == "spot" for s in out)            # perp -> spot
    assert all(s.leverage == 1.0 for s in out)            # no leverage


def test_enforce_spot_only_passes_native_spot_through():
    s = _sig("long", venue="spot", leverage=1.0, name="donchian_breakout")
    out = enforce_spot_only([s])
    assert len(out) == 1 and out[0].venue == "spot" and out[0].leverage == 1.0


def test_enforce_spot_only_drops_an_all_short_batch():
    assert enforce_spot_only([_sig("short"), _sig("short")]) == []


@pytest.mark.anyio
async def test_open_perp_blocked_in_spot_only_mode():
    # The hackathon default. Even a sim executor must refuse to open a perp.
    assert settings.spot_only is True
    ex = TwakExecutor(settings, simulation=True)
    res = await ex.open_perp("ETH", "long", Decimal("10"), 3.0, ref_price=3000.0)
    assert res.success is False
    assert "spot_only" in (res.error or "").lower()
