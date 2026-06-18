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
    "rsi_reversion": RsiReversionStrategy,
    "xsect_momentum": CrossSectionalMomentumStrategy,
}

def active_strategies(settings, suspended_list: List[str] = None) -> List[BaseStrategy]:
    """
    Returns only strategies that are:
    (a) enabled in settings
    (b) not in the arbiter's suspended list.
    """
    if suspended_list is None:
        suspended_list = []
        
    active = []
    
    if settings.enable_strategy_momentum_pullback and "momentum_pullback" not in suspended_list:
        active.append(MomentumPullbackStrategy())
    if settings.enable_strategy_fib_golden_pocket and "fib_golden_pocket" not in suspended_list:
        active.append(FibGoldenPocketStrategy())
    if settings.enable_strategy_capitulation and "capitulation" not in suspended_list:
        active.append(CapitulationStrategy())
    if settings.enable_strategy_news_catalyst and "news_catalyst" not in suspended_list:
        active.append(NewsCatalystStrategy())
    if settings.enable_strategy_mean_reversion and "mean_reversion" not in suspended_list:
        active.append(MeanReversionStrategy())
    if settings.enable_strategy_trend_follow and "trend_follow" not in suspended_list:
        active.append(TrendFollowStrategy())
    if settings.enable_strategy_vol_squeeze and "vol_squeeze" not in suspended_list:
        active.append(VolSqueezeStrategy())
    if settings.enable_strategy_whale_flow and "whale_flow" not in suspended_list:
        active.append(WhaleFlowStrategy())
    if settings.enable_strategy_donchian_breakout and "donchian_breakout" not in suspended_list:
        active.append(DonchianBreakoutStrategy())
    if getattr(settings, "enable_strategy_donchian_perp", True) and "donchian_perp" not in suspended_list:
        active.append(DonchianPerpStrategy())
    if getattr(settings, "enable_strategy_salamander_perp", True) and "salamander_perp" not in suspended_list:
        active.append(SalamanderPerpStrategy())
    if getattr(settings, "enable_strategy_supertrend_perp", False) and "supertrend_perp" not in suspended_list:
        active.append(SupertrendPerpStrategy())
    if settings.enable_strategy_rsi_reversion and "rsi_reversion" not in suspended_list:
        active.append(RsiReversionStrategy())
    if settings.enable_strategy_xsect_momentum and "xsect_momentum" not in suspended_list:
        active.append(CrossSectionalMomentumStrategy())

    return active
