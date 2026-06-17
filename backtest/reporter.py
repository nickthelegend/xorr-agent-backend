from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Dict, Any
import numpy as np

from backtest.engine import BacktestTrade

@dataclass
class StrategyBacktest:
    trades: int
    win_rate: float
    expectancy_r: float
    pnl_usd: float

@dataclass
class SymbolBacktest:
    trades: int
    win_rate: float
    expectancy_r: float
    pnl_usd: float

@dataclass
class BacktestReport:
    run_id: str
    started_at: str
    ended_at: str
    window_days: int
    quality_mode: bool
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    expectancy_r: float
    total_pnl_pct: float
    max_drawdown_pct: float
    sharpe: float
    profit_factor: float
    by_strategy: Dict[str, StrategyBacktest]
    by_symbol: Dict[str, SymbolBacktest]
    equity_curve: List[Dict[str, Any]]

def compile_report(
    run_id: str,
    started_at: datetime,
    ended_at: datetime,
    window_days: int,
    quality_mode: bool,
    trades: List[BacktestTrade],
    equity_curve: List[Dict[str, Any]]
) -> BacktestReport:
    """Compiles a full BacktestReport from the list of executed trades and equity points."""
    # Filter out shadow/paper trades for actual performance metrics
    real_trades = [t for t in trades if not t.is_shadow]
    
    total = len(real_trades)
    wins = sum(1 for t in real_trades if t.pnl_usd > 0)
    losses = sum(1 for t in real_trades if t.pnl_usd < 0)
    
    win_rate = (wins / total * 100.0) if total > 0 else 0.0
    
    # Expectancy in R-multiples
    # E = WR * avg_win_R - (1-WR) * avg_loss_R
    # (Since R can be positive or negative, mean of realized R values directly gives the expectancy!)
    expectancy_r = float(np.mean([t.r_realized for t in real_trades])) if total > 0 else 0.0
    
    # Gross wins and losses for Profit Factor
    gross_wins = sum(t.pnl_usd for t in real_trades if t.pnl_usd > 0)
    gross_losses = sum(abs(t.pnl_usd) for t in real_trades if t.pnl_usd < 0)
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else (gross_wins if gross_wins > 0 else 1.0)
    
    # Total PnL percentage
    # Initial balance is assumed $100
    pnl_usd = sum(t.pnl_usd for t in real_trades)
    total_pnl_pct = pnl_usd  # Since initial is 100
    
    # Max Drawdown and Sharpe based on equity curve
    # Let's reconstruct daily/hourly points or just use the segment end equity values
    equities = [100.0] + [pt["equity"] for pt in equity_curve]
    
    # Max Drawdown
    peak = 100.0
    max_dd = 0.0
    for eq in equities:
        peak = max(peak, eq)
        dd = ((peak - eq) / peak) * 100.0
        max_dd = max(max_dd, dd)
        
    # Sharpe Ratio (approximate from trade PnLs)
    if total > 1:
        trade_returns = [t.pnl_pct for t in real_trades]
        mean_ret = np.mean(trade_returns)
        std_ret = np.std(trade_returns)
        sharpe = (mean_ret / std_ret * np.sqrt(total)) if std_ret > 0 else 0.0
    else:
        sharpe = 0.0
        
    # Strategy breakdown
    by_strategy = {}
    strategies_seen = set(t.strategy for t in real_trades)
    for strat in strategies_seen:
        s_trades = [t for t in real_trades if t.strategy == strat]
        s_total = len(s_trades)
        s_wins = sum(1 for t in s_trades if t.pnl_usd > 0)
        s_wr = (s_wins / s_total * 100.0) if s_total > 0 else 0.0
        s_exp = float(np.mean([t.r_realized for t in s_trades])) if s_total > 0 else 0.0
        s_pnl = sum(t.pnl_usd for t in s_trades)
        by_strategy[strat] = StrategyBacktest(
            trades=s_total,
            win_rate=round(s_wr, 2),
            expectancy_r=round(s_exp, 2),
            pnl_usd=round(s_pnl, 2)
        )
        
    # Symbol breakdown
    by_symbol = {}
    symbols_seen = set(t.symbol for t in real_trades)
    for sym in symbols_seen:
        sym_trades = [t for t in real_trades if t.symbol == sym]
        sym_total = len(sym_trades)
        sym_wins = sum(1 for t in sym_trades if t.pnl_usd > 0)
        sym_wr = (sym_wins / sym_total * 100.0) if sym_total > 0 else 0.0
        sym_exp = float(np.mean([t.r_realized for t in sym_trades])) if sym_total > 0 else 0.0
        sym_pnl = sum(t.pnl_usd for t in sym_trades)
        by_symbol[sym] = SymbolBacktest(
            trades=sym_total,
            win_rate=round(sym_wr, 2),
            expectancy_r=round(sym_exp, 2),
            pnl_usd=round(sym_pnl, 2)
        )
        
    return BacktestReport(
        run_id=run_id,
        started_at=started_at.isoformat(),
        ended_at=ended_at.isoformat(),
        window_days=window_days,
        quality_mode=quality_mode,
        total_trades=total,
        wins=wins,
        losses=losses,
        win_rate=round(win_rate, 2),
        expectancy_r=round(expectancy_r, 2),
        total_pnl_pct=round(total_pnl_pct, 2),
        max_drawdown_pct=round(max_dd, 2),
        sharpe=round(sharpe, 2),
        profit_factor=round(profit_factor, 2),
        by_strategy=by_strategy,
        by_symbol=by_symbol,
        equity_curve=equity_curve
    )
