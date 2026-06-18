from typing import List
from config import settings
from strategies.base import BaseStrategy
from strategies.momentum_pullback import MomentumPullbackStrategy
from strategies.fib_golden_pocket import FibGoldenPocketStrategy
from strategies.capitulation import CapitulationStrategy
from strategies.news_catalyst import NewsCatalystStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.trend_follow import TrendFollowStrategy
from strategies.vol_squeeze import VolSqueezeStrategy
from strategies.whale_flow import WhaleFlowStrategy
from strategies.donchian_breakout import DonchianBreakoutStrategy
from strategies.donchian_perp import DonchianPerpStrategy
from strategies.salamander_perp import SalamanderPerpStrategy
from strategies.supertrend_perp import SupertrendPerpStrategy
from strategies.volsqueeze_perp import VolSqueezePerpStrategy
from strategies.rsi_div_perp import RsiDivPerpStrategy
from strategies.liq_flow_perp import (
    LiqReversionPerpStrategy, LiqZscorePerpStrategy, LiqRelspikePerpStrategy,
)
from strategies.macd_perp import (
    MacdRegimePerpStrategy, LiqMacdMomentumPerpStrategy, MacdLiqReversalPerpStrategy,
)
from strategies.rsi_reversion import RsiReversionStrategy
from strategies.cross_sectional_momentum import CrossSectionalMomentumStrategy

STRATEGIES = {
    "momentum_pullback": MomentumPullbackStrategy,
    "fib_golden_pocket": FibGoldenPocketStrategy,
    "capitulation": CapitulationStrategy,
    "news_catalyst": NewsCatalystStrategy,
    "mean_reversion": MeanReversionStrategy,
    "trend_follow": TrendFollowStrategy,
    "vol_squeeze": VolSqueezeStrategy,
    "whale_flow": WhaleFlowStrategy,
    "donchian_breakout": DonchianBreakoutStrategy,
    "donchian_perp": DonchianPerpStrategy,
    "salamander_perp": SalamanderPerpStrategy,
    "supertrend_perp": SupertrendPerpStrategy,
    "volsqueeze_perp": VolSqueezePerpStrategy,
    "rsi_div_perp": RsiDivPerpStrategy,
    "liq_reversion_perp": LiqReversionPerpStrategy,
    "liq_zscore_perp": LiqZscorePerpStrategy,
    "liq_relspike_perp": LiqRelspikePerpStrategy,
    "macd_regime_perp": MacdRegimePerpStrategy,
    "liq_macd_momentum_perp": LiqMacdMomentumPerpStrategy,
    "macd_liq_reversal_perp": MacdLiqReversalPerpStrategy,
    "rsi_reversion": RsiReversionStrategy,
    "xsect_momentum": CrossSectionalMomentumStrategy,
}

# 10 liquidation + trend-break ideas (registered; enabled per-backtest below)
from strategies.liq_trend_ideas import IDEAS as _LIQ_TREND_IDEAS
STRATEGIES.update(_LIQ_TREND_IDEAS)

def active_strategies(settings, suspended_list: List[str] = None,
                      promoted_list: List[str] = None) -> List[BaseStrategy]:
    """Strategies that are ACTIVE this scan: (config-enabled OR arbiter-promoted
    from shadow) AND not arbiter-suspended. Every flag follows enable_strategy_<key>."""
    suspended = set(suspended_list or [])
    promoted = set(promoted_list or [])
    active = []
    for name, cls in STRATEGIES.items():
        enabled = bool(getattr(settings, f"enable_strategy_{name}", False)) or name in promoted
        if enabled and name not in suspended:
            active.append(cls())
    return active


def shadow_test_names(settings, active_names) -> List[str]:
    """Registered strategies to run as paper SHADOW this scan: the configured
    shadow_test_strategies that aren't already active (don't shadow what's live)."""
    raw = getattr(settings, "shadow_test_strategies", "") or ""
    want = [s.strip() for s in raw.split(",") if s.strip()]
    active = set(active_names)
    return [n for n in want if n in STRATEGIES and n not in active]
