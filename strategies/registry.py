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

STRATEGIES = {
    "momentum_pullback": MomentumPullbackStrategy,
    "fib_golden_pocket": FibGoldenPocketStrategy,
    "capitulation": CapitulationStrategy,
    "news_catalyst": NewsCatalystStrategy,
    "mean_reversion": MeanReversionStrategy,
    "trend_follow": TrendFollowStrategy,
    "vol_squeeze": VolSqueezeStrategy,
    "whale_flow": WhaleFlowStrategy,
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
        
    return active
