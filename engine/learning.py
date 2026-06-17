import logging
import json
from datetime import datetime, timezone, time
from typing import Dict, List
from sqlmodel import Session, select

from persistence.db import engine as db_engine
from persistence.models import StrategyStat, Trade
from strategies.registry import STRATEGIES

logger = logging.getLogger("xorr.engine.learning")

# Memory cache for weights, defaulted to 1.0
STRATEGY_WEIGHTS: Dict[str, float] = {name: 1.0 for name in STRATEGIES.keys()}

def record_trade_outcome(session: Session, trade_id: str, strategy_name: str, pnl_usd: float, invested: float, stop_loss_pct: float):
    """
    Records a closed trade's outcome and calculates its realized R-multiple.
    R_realized = realized PnL / initial risk.
    """
    initial_risk = invested * (stop_loss_pct / 100.0)
    if initial_risk <= 0:
        r_realized = 0.0
    else:
        r_realized = pnl_usd / initial_risk
        
    stat = StrategyStat(
        strategy=strategy_name,
        trade_id=trade_id,
        closed_at=datetime.now(timezone.utc),
        r_realized=r_realized,
        pnl_usd=pnl_usd
    )
    session.add(stat)
    session.commit()
    logger.info(f"[LEARNING] Recorded trade {trade_id} outcome for {strategy_name}: realized PnL=${pnl_usd:.2f}, R_realized={r_realized:.2f}R")

def get_expectancy(strategy_name: str) -> float:
    """Returns the rolling 20-trade expectancy (in R-multiples) for a strategy."""
    with Session(db_engine) as session:
        statement = select(StrategyStat).where(StrategyStat.strategy == strategy_name).order_by(StrategyStat.closed_at.desc()).limit(20)
        stats = list(session.exec(statement).all())
        if not stats:
            return 0.0
        return sum(s.r_realized for s in stats) / len(stats)

def is_symbol_soft_blacklisted(symbol: str) -> bool:
    """
    Deterministically simulates soft-blacklist state machine from Trade history.
    - If rolling 10-trade win rate < 30% -> blacklisted.
    - Hysteresis: lifted after 5 consecutive winning trades since blacklist trigger.
    """
    with Session(db_engine) as session:
        statement = select(Trade).where(Trade.symbol == symbol.upper(), Trade.status != "open").order_by(Trade.opened_at.asc())
        trades = list(session.exec(statement).all())
        if len(trades) < 10:
            return False
            
        blacklisted = False
        win_streak = 0
        
        for i in range(len(trades)):
            t = trades[i]
            if blacklisted:
                if t.status == "win":
                    win_streak += 1
                    if win_streak >= 5:
                        blacklisted = False
                        win_streak = 0
                else:
                    win_streak = 0
            else:
                if i >= 9:
                    window = trades[i-9:i+1]
                    wins = sum(1 for w in window if w.status == "win")
                    wr = wins / 10.0
                    if wr < 0.30:
                        blacklisted = True
                        win_streak = 0
                        
        return blacklisted

def rebalance_weights():
    """
    Recomputes strategy weights based on last 50 trades' expectancy.
    weight = max(0.1, min(2.0, 1.0 + expectancy_r))
    """
    with Session(db_engine) as session:
        for strat in STRATEGIES.keys():
            statement = select(StrategyStat).where(StrategyStat.strategy == strat).order_by(StrategyStat.closed_at.desc()).limit(50)
            stats = list(session.exec(statement).all())
            if not stats:
                STRATEGY_WEIGHTS[strat] = 1.0
            else:
                E = sum(s.r_realized for s in stats) / len(stats)
                weight = max(0.1, min(2.0, 1.0 + E))
                STRATEGY_WEIGHTS[strat] = round(weight, 3)
                
    logger.info(f"[LEARNING] Strategy weights rebalanced: {STRATEGY_WEIGHTS}")

def get_weight(strategy_name: str) -> float:
    """Returns the current weight multiplier for position sizing of a strategy."""
    return STRATEGY_WEIGHTS.get(strategy_name, 1.0)
