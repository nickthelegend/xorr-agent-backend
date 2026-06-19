"""Risk-free exit math — the R-multiple + breakeven/trail price helpers the monitor uses."""
from types import SimpleNamespace

from engine.monitor import _r_multiple, _price_for_r


def _pos(entry, init_stop, direction="long"):
    return SimpleNamespace(entry_price=entry, init_stop=init_stop,
                           stop_loss=init_stop, direction=direction)


def test_r_multiple_long():
    pos = _pos(100.0, 95.0)                 # 5% stop = 1R
    assert abs(_r_multiple(pos, 100.0) - 0.0) < 1e-6
    assert abs(_r_multiple(pos, 105.0) - 1.0) < 1e-6     # +5% = +1R
    assert abs(_r_multiple(pos, 110.0) - 2.0) < 1e-6
    assert _r_multiple(pos, 95.0) < 0                     # back to stop = -1R


def test_price_for_r_long():
    pos = _pos(100.0, 95.0)
    assert abs(_price_for_r(pos, 0.0) - 100.0) < 1e-6     # 0R = breakeven (entry)
    assert abs(_price_for_r(pos, 1.0) - 105.0) < 1e-6
    assert abs(_price_for_r(pos, 2.0) - 110.0) < 1e-6


def test_short_side_r_and_price():
    pos = _pos(100.0, 105.0, "short")        # stop ABOVE = 5% = 1R
    assert abs(_r_multiple(pos, 95.0) - 1.0) < 1e-6      # down 5% = +1R for a short
    assert abs(_price_for_r(pos, 1.0) - 95.0) < 1e-6
    assert abs(_price_for_r(pos, 0.0) - 100.0) < 1e-6


def test_trail_locks_in_profit_monotonically():
    # at +3R, trailing with 0.8R giveback should lock in 2.2R worth of price
    pos = _pos(100.0, 95.0)
    locked = _price_for_r(pos, 3.0 - 0.8)
    assert abs(locked - (100.0 + 2.2 * 5.0)) < 1e-6      # 100 + 2.2R*(5%) = 111.0
    assert locked > pos.entry_price                       # always above entry = real profit
