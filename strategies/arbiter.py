import uuid
import logging
from datetime import datetime, timezone
from sqlmodel import Session, select
from typing import List, Set, Dict

from core.types import Signal, DecisionLog
from persistence.models import Trade, Position
from config import settings
from brain.decision_log import log_decision

logger = logging.getLogger("xorr.strategies.arbiter")

class StrategyArbiter:
    def __init__(self):
        # Set of suspended strategy names
        self.suspended_strategies: Set[str] = set()
        # Disabled strategies PROMOTED to active because their shadow track proved out
        self.promoted_strategies: Set[str] = set()

    def evaluate_promotions(self, session: Session):
        """Promote a shadow-tested (disabled) strategy to active once its paper
        track proves out (>=8 shadow trades, expectancy >0.25R); demote a promoted
        strategy if its REAL track then bleeds (>=10 trades, E<0)."""
        from persistence.models import StrategyStat
        raw = getattr(settings, "shadow_test_strategies", "") or ""
        shadow_names = [s.strip() for s in raw.split(",") if s.strip()]

        for name in shadow_names:
            if name in self.promoted_strategies:
                continue
            stmt = select(StrategyStat).where(StrategyStat.strategy == f"shadow_{name}").order_by(StrategyStat.closed_at.desc()).limit(12)
            stats = list(session.exec(stmt).all())
            if len(stats) >= 8:
                e = sum(s.r_realized for s in stats[:8]) / 8.0
                if e > 0.25:
                    self.promoted_strategies.add(name)
                    logger.info(f"[ARBITER] PROMOTE shadow '{name}' to active (shadow E={e:.2f}R)")
                    log_decision(DecisionLog(id=str(uuid.uuid4()), t=datetime.now(timezone.utc),
                                             symbol="SYS", action="PROMOTE", strategy=name,
                                             reasoning=f"Shadow expectancy {e:.2f}R over 8 trades > 0.25R. Promoting to live."))

        for name in list(self.promoted_strategies):
            stmt = select(StrategyStat).where(StrategyStat.strategy == name).order_by(StrategyStat.closed_at.desc()).limit(12)
            stats = list(session.exec(stmt).all())
            if len(stats) >= 10:
                e = sum(s.r_realized for s in stats[:10]) / 10.0
                if e < 0.0:
                    self.promoted_strategies.discard(name)
                    logger.info(f"[ARBITER] DEMOTE promoted '{name}' (real E={e:.2f}R < 0)")
                    log_decision(DecisionLog(id=str(uuid.uuid4()), t=datetime.now(timezone.utc),
                                             symbol="SYS", action="DEMOTE", strategy=name,
                                             reasoning=f"Promoted strategy real expectancy {e:.2f}R < 0. Back to shadow."))

    def update_suspended_states(self, session: Session):
        """
        Updates strategy suspension status:
        - If rolling 20-trade expectancy E < 0 and N >= 10 -> SUSPEND.
        - Hysteresis: re-enable if 5 shadow trades have expectancy E_shadow > 0.15R.
        - Hard guard: at least 3 strategies must remain active.
        """
        from strategies.registry import STRATEGIES
        strats = list(STRATEGIES.keys())

        # 1. Recompute expectancies for all strategies from StrategyStat table
        from persistence.models import StrategyStat
        expectancies = {}
        trade_counts = {}
        
        for strat in strats:
            # Count real closed trades
            trade_stmt = select(Trade).where(Trade.strategy == strat).where(Trade.status != "open")
            closed_trades = list(session.exec(trade_stmt).all())
            trade_counts[strat] = len(closed_trades)

            # Rolling 20 expectancy
            stat_stmt = select(StrategyStat).where(StrategyStat.strategy == strat).order_by(StrategyStat.closed_at.desc()).limit(20)
            stats = list(session.exec(stat_stmt).all())
            if not stats:
                expectancies[strat] = 0.0
            else:
                expectancies[strat] = sum(s.r_realized for s in stats) / len(stats)

        # 2. Check for shadow revives
        revived_this_cycle = set()
        for strat in list(self.suspended_strategies):
            shadow_strat = f"shadow_{strat}"
            
            # Count completed shadow trades
            shadow_trade_stmt = select(Trade).where(Trade.strategy == shadow_strat).where(Trade.status != "open")
            closed_shadows = list(session.exec(shadow_trade_stmt).all())
            
            if len(closed_shadows) >= 8:
                # Query last 8 shadow stats — require a sustained positive shadow run
                shadow_stat_stmt = select(StrategyStat).where(StrategyStat.strategy == shadow_strat).order_by(StrategyStat.closed_at.desc()).limit(8)
                shadow_stats = list(session.exec(shadow_stat_stmt).all())
                shadow_e = sum(s.r_realized for s in shadow_stats) / len(shadow_stats) if shadow_stats else 0.0

                if shadow_e > 0.25:
                    self.suspended_strategies.discard(strat)
                    revived_this_cycle.add(strat)
                    logger.info(f"[ARBITER] REVIVE strategy '{strat}': shadow expectancy {shadow_e:.2f}R > 0.15R")
                    
                    # Promote last 5 shadow trades/stats to real trades to give a fresh positive baseline
                    try:
                        # 1. Delete all old real stats and trades for this strategy
                        from sqlmodel import delete
                        session.execute(delete(StrategyStat).where(StrategyStat.strategy == strat))
                        session.execute(delete(Trade).where(Trade.strategy == strat))
                        
                        # 2. Promote StrategyStat
                        stat_stmt = select(StrategyStat).where(StrategyStat.strategy == shadow_strat).order_by(StrategyStat.closed_at.desc()).limit(5)
                        for s_stat in session.exec(stat_stmt).all():
                            s_stat.strategy = strat
                            session.add(s_stat)
                        # 3. Promote Trade
                        trade_stmt = select(Trade).where(Trade.strategy == shadow_strat).order_by(Trade.closed_at.desc()).limit(5)
                        for s_trade in session.exec(trade_stmt).all():
                            s_trade.strategy = strat
                            session.add(s_trade)
                        session.commit()
                    except Exception as ex:
                        logger.warning(f"Failed to migrate shadow trades on revive: {ex}")

                    log_decision(DecisionLog(
                        id=str(uuid.uuid4()),
                        t=datetime.now(timezone.utc),
                        symbol="SYS",
                        action="REVIVE",
                        strategy=strat,
                        reasoning=f"Shadow expectancy {shadow_e:.2f}R > 0.15R. Reviving strategy and promoting shadow history."
                    ))

        # 3. Check for suspensions
        active_count = len(strats) - len(self.suspended_strategies)
        
        # Sort strategies by expectancy (worst performing first)
        sorted_strats = sorted(strats, key=lambda s: expectancies.get(s, 0.0))
        
        for strat in sorted_strats:
            if strat in self.suspended_strategies or strat in revived_this_cycle:
                continue
                
            E = expectancies[strat]
            n_trades = trade_counts[strat]
            
            if E < 0.0 and n_trades >= 10:
                # Apply hard guardfloor
                if active_count > 3:
                    self.suspended_strategies.add(strat)
                    active_count -= 1
                    logger.info(f"[ARBITER] SUSPEND strategy '{strat}': rolling expectancy {E:.2f}R < 0.0R over {n_trades} trades")
                    log_decision(DecisionLog(
                        id=str(uuid.uuid4()),
                        t=datetime.now(timezone.utc),
                        symbol="SYS",
                        action="SUSPEND",
                        strategy=strat,
                        reasoning=f"Rolling expectancy {E:.2f}R < 0.0R. Suspending strategy."
                    ))
                else:
                    logger.warning(f"[ARBITER GUARD] Cannot suspend '{strat}' (E={E:.2f}R): active count would drop below 3.")

    def filter(self, session: Session, signals: List[Signal], open_positions: List[Position]) -> List[Signal]:
        """
        Filters signals based on suspension states and symbol concentration caps.
        """
        self.update_suspended_states(session)
        self.evaluate_promotions(session)

        base_size = settings.base_trade_size_usd
        total_deployed = sum(p.invested for p in open_positions)
        
        symbol_risk = {}
        for p in open_positions:
            symbol_risk[p.symbol.upper()] = symbol_risk.get(p.symbol.upper(), 0.0) + p.invested

        final_signals = []
        for sig in signals:
            # If strategy is suspended, route to shadow in the pipeline
            if sig.strategy_name in self.suspended_strategies:
                continue

            # Concentration Cap (50% per symbol)
            current_sym_risk = symbol_risk.get(sig.symbol.upper(), 0.0)
            new_sym_risk = current_sym_risk + base_size
            new_total = total_deployed + base_size
            
            if new_total > 0:
                risk_ratio = new_sym_risk / new_total
                if risk_ratio > 0.50 and len(open_positions) > 0:
                    logger.warning(f"[ARBITER REJECT] Blocked signal for {sig.symbol} via {sig.strategy_name}: risk ratio {risk_ratio:.1%} would exceed 50% cap.")
                    log_decision(DecisionLog(
                        id=str(uuid.uuid4()),
                        t=datetime.now(timezone.utc),
                        symbol=sig.symbol,
                        action="ARBITER_REJECT",
                        strategy=sig.strategy_name,
                        reasoning=f"Concentration cap breach: symbol risk {risk_ratio:.1%} would exceed 50%"
                    ))
                    continue

            final_signals.append(sig)

        return final_signals

# Singleton instance
arbiter = StrategyArbiter()
