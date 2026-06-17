import logging
from fastapi import APIRouter, Depends
from sqlmodel import Session

from api.deps import get_session
from engine.learning import get_expectancy, get_weight, is_symbol_soft_blacklisted
from strategies.registry import STRATEGIES
from strategies.arbiter import arbiter
from data.tokens import iter_all

logger = logging.getLogger("xorr.api.learning")
router = APIRouter()

@router.get("/learning/stats")
async def get_learning_stats():
    """Returns per-strategy expectancy/weights and symbol blacklist status."""
    stats = {}
    for name in STRATEGIES.keys():
        stats[name] = {
            "expectancy": round(get_expectancy(name), 3),
            "weight": round(get_weight(name), 3),
            "suspended": name in arbiter.suspended_strategies
        }

    blacklisted = []
    for token in iter_all():
        if is_symbol_soft_blacklisted(token.symbol):
            blacklisted.append(token.symbol)

    return {
        "strategies": stats,
        "blacklisted_symbols": blacklisted
    }
