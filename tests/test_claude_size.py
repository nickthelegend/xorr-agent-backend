"""Claude pick sizing — concentrate real capital, capped to protect the DQ gate, and never
drop a real pick to the Fear&Greed dropout that bit us before."""
from risk.sizing import calculate_claude_size
from config import settings


def test_real_pick_clears_floor_and_is_meaningful():
    # a real pick deploys real capital (not $2 dust) and clears the on-chain minimum
    s = calculate_claude_size(available_usdt=42.0, deployed_usd=0.0,
                              active_position_count=0, conviction=0.6, drawdown_multiplier=1.0)
    assert s >= 1.10 and s >= 5.0          # base $7 * ~1.28 conv, capped to 25% of $42 = $10.5


def test_size_scales_with_conviction():
    lo = calculate_claude_size(42.0, 0.0, 0, 0.45, 1.0)
    hi = calculate_claude_size(42.0, 0.0, 0, 0.90, 1.0)
    assert hi > lo


def test_per_position_cap_binds():
    # high conviction on a $30 book: raw size (~$11) is capped to 25% of trading capital ($7.5)
    s = calculate_claude_size(available_usdt=30.0, deployed_usd=0.0,
                              active_position_count=0, conviction=1.0, drawdown_multiplier=1.0)
    assert abs(s - settings.claude_max_position_pct * 30.0) < 0.05    # == $7.5, the per-pos cap


def test_total_deploy_cap_blocks_overexposure():
    # already over the total-deploy cap (deployed $30 of ~$35 trading capital) -> no room -> skip
    s = calculate_claude_size(available_usdt=5.0, deployed_usd=30.0,
                              active_position_count=2, conviction=0.9, drawdown_multiplier=1.0)
    assert s == 0.0


def test_drawdown_ladder_can_stand_down():
    assert calculate_claude_size(42.0, 0.0, 0, 0.9, 0.0) == 0.0


def test_respects_concurrency_cap():
    assert calculate_claude_size(42.0, 0.0, settings.max_concurrent_positions, 0.9, 1.0) == 0.0


def test_capped_by_available_cash():
    s = calculate_claude_size(available_usdt=0.5, deployed_usd=0.0,
                              active_position_count=0, conviction=0.9, drawdown_multiplier=1.0)
    assert s == 0.0      # only $0.50 free -> below the $1.10 minimum -> skip
