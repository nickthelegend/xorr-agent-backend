from sqlmodel import Session, select
from typing import List, Dict, Set
from core.types import Signal
from persistence.models import Trade, Position
from config import settings

class StrategyArbiter:
    def __init__(self):
        # Keeps track of strategy statuses in memory: True if disabled
        self._disabled_strategies: Set[str] = set()

    def filter(self, session: Session, signals: List[Signal], open_positions: List[Position]) -> List[Signal]:
        """
        Enforces:
          1. Diversity floor (<= 2 open positions per strategy)
          2. Concentration cap (no single symbol > 50% of total risked capital)
          3. Performance auto-kill (disable strategy if rolling 20-trade win rate < 22%, re-enable at >= 28%)
        """
        # --- 1. Enforce Strategy Auto-Kill (Performance) ---
        # Get unique strategy names from signals
        strategies_to_check = set(s.strategy_name for s in signals)
        for strat in list(strategies_to_check):
            # Query last 20 closed trades of this strategy
            statement = select(Trade).where(Trade.strategy == strat).where(Trade.status != "open").order_by(Trade.closed_at.desc()).limit(20)
            closed_trades = list(session.exec(statement).all())
            
            if len(closed_trades) >= 5:  # Require at least 5 trades to activate auto-kill
                wins = sum(1 for t in closed_trades if t.pnl_usd > 0)
                win_rate = wins / len(closed_trades)
                
                # Check performance thresholds
                is_disabled = strat in self._disabled_strategies
                if not is_disabled and win_rate < 0.22:
                    print(f"[ARBITER KILL] Disabling strategy '{strat}' due to poor performance: win rate={win_rate*100:.1f}% (limit < 22%)")
                    self._disabled_strategies.add(strat)
                elif is_disabled and win_rate >= 0.28:
                    print(f"[ARBITER RE-ENABLE] Re-enabling strategy '{strat}': win rate={win_rate*100:.1f}% (threshold >= 28%)")
                    self._disabled_strategies.discard(strat)
            
        # Filter out signals from disabled strategies
        active_signals = [s for s in signals if s.strategy_name not in self._disabled_strategies]
        
        # --- 2. Enforce Diversity Floor (<= 2 positions per strategy) ---
        strategy_counts: Dict[str, int] = {}
        for pos in open_positions:
            strategy_counts[pos.strategy] = strategy_counts.get(pos.strategy, 0) + 1
            
        filtered_by_diversity = []
        for sig in active_signals:
            current_count = strategy_counts.get(sig.strategy_name, 0)
            if current_count >= 2:
                print(f"[ARBITER REJECT] Blocked signal for {sig.symbol} via '{sig.strategy_name}' (already has {current_count} active positions; diversity limit is 2)")
                continue
            filtered_by_diversity.append(sig)
            
        # --- 3. Enforce Concentration Cap (no single symbol > 50% of risked capital) ---
        base_size = settings.base_trade_size_usd
        total_risk = sum(p.invested for p in open_positions) + base_size
        
        # Map out current allocations per symbol
        symbol_risk: Dict[str, float] = {}
        for pos in open_positions:
            symbol_risk[pos.symbol] = symbol_risk.get(pos.symbol, 0.0) + pos.invested
            
        final_signals = []
        for sig in filtered_by_diversity:
            new_sym_risk = symbol_risk.get(sig.symbol, 0.0) + base_size
            risk_ratio = new_sym_risk / total_risk
            
            if risk_ratio > 0.50 and len(open_positions) > 0:
                print(f"[ARBITER REJECT] Blocked signal for {sig.symbol}: concentration ratio {risk_ratio*100:.1f}% would exceed 50% limit.")
                continue
            final_signals.append(sig)
            
        return final_signals

# Singleton instance
arbiter = StrategyArbiter()
