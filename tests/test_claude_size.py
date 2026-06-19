"""Claude pick sizing — must reliably size a real pick (no Fear&Greed dropout) while
keeping the drawdown-ladder and concurrency DQ protections."""
from risk.sizing import calculate_claude_size
from config import settings


def test_real_pick_always_clears_floor():
    # even a low-conviction pick clears the on-chain dust minimum (the F&G dropout bug)
    s = calculate_claude_size(available_usdt=42.0, active_position_count=0,
                              conviction=0.45, drawdown_multiplier=1.0)
    assert s >= 1.10


def test_size_scales_with_conviction():
    lo = calculate_claude_size(42.0, 0, 0.45, 1.0)
    hi = calculate_claude_size(42.0, 0, 0.90, 1.0)
    assert hi > lo


def test_drawdown_ladder_can_stand_down():
    # the DQ-gate drawdown ladder still wins: multiplier 0 => no trade
    assert calculate_claude_size(42.0, 0, 0.9, 0.0) == 0.0


def test_respects_concurrency_cap():
    assert calculate_claude_size(42.0, settings.max_concurrent_positions, 0.9, 1.0) == 0.0


def test_capped_by_available_cash():
    s = calculate_claude_size(available_usdt=0.5, active_position_count=0,
                              conviction=0.9, drawdown_multiplier=1.0)
    assert s == 0.0      # only $0.50 free -> below the $1.10 minimum -> skip
