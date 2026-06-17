import json
from datetime import datetime
from sqlmodel import Session, select
from typing import List, Optional
from persistence.db import engine as db_engine
from persistence.models import BacktestRun
from backtest.reporter import BacktestReport, StrategyBacktest, SymbolBacktest

def save_backtest_run(report: BacktestReport):
    """Saves a BacktestReport to the SQLite database."""
    # Convert dataclass to dict
    from dataclasses import asdict
    report_dict = asdict(report)
    
    with Session(db_engine) as session:
        db_run = BacktestRun(
            run_id=report.run_id,
            started_at=datetime.fromisoformat(report.started_at),
            ended_at=datetime.fromisoformat(report.ended_at),
            window_days=report.window_days,
            quality_mode=report.quality_mode,
            report_json=json.dumps(report_dict)
        )
        session.merge(db_run)
        session.commit()

def load_backtest_run(run_id: str) -> Optional[BacktestReport]:
    """Loads a BacktestReport by its run_id."""
    with Session(db_engine) as session:
        db_run = session.get(BacktestRun, run_id)
        if not db_run:
            return None
            
        data = json.loads(db_run.report_json)
        
        # Reconstruct custom classes
        by_strat = {}
        for k, v in data.get("by_strategy", {}).items():
            by_strat[k] = StrategyBacktest(**v)
            
        by_sym = {}
        for k, v in data.get("by_symbol", {}).items():
            by_sym[k] = SymbolBacktest(**v)
            
        return BacktestReport(
            run_id=data["run_id"],
            started_at=data["started_at"],
            ended_at=data["ended_at"],
            window_days=data["window_days"],
            quality_mode=data["quality_mode"],
            total_trades=data["total_trades"],
            wins=data["wins"],
            losses=data["losses"],
            win_rate=data["win_rate"],
            expectancy_r=data["expectancy_r"],
            total_pnl_pct=data["total_pnl_pct"],
            max_drawdown_pct=data["max_drawdown_pct"],
            sharpe=data["sharpe"],
            profit_factor=data["profit_factor"],
            by_strategy=by_strat,
            by_symbol=by_sym,
            equity_curve=data["equity_curve"]
        )

def list_backtest_runs() -> List[dict]:
    """Lists summaries of all historical backtest runs."""
    with Session(db_engine) as session:
        statement = select(BacktestRun).order_by(BacktestRun.started_at.desc())
        runs = session.exec(statement).all()
        summaries = []
        for r in runs:
            try:
                data = json.loads(r.report_json)
                summaries.append({
                    "run_id": r.run_id,
                    "started_at": r.started_at.isoformat(),
                    "ended_at": r.ended_at.isoformat(),
                    "window_days": r.window_days,
                    "quality_mode": r.quality_mode,
                    "total_trades": data.get("total_trades", 0),
                    "win_rate": data.get("win_rate", 0.0),
                    "expectancy_r": data.get("expectancy_r", 0.0),
                    "total_pnl_pct": data.get("total_pnl_pct", 0.0),
                    "max_drawdown_pct": data.get("max_drawdown_pct", 0.0)
                })
            except Exception:
                pass
        return summaries
