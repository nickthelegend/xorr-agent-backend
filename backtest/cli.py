import asyncio
import argparse
import sys
import json
from datetime import datetime, timezone

# Windows consoles default to cp1252; make sure non-ASCII token symbols never
# crash the CLI output.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backtest.runner import run_walk_forward_backtest
from backtest.store import save_backtest_run, load_backtest_run, list_backtest_runs
from strategies.registry import STRATEGIES
from data.tokens import iter_tradable

def parse_args():
    parser = argparse.ArgumentParser(description="XORR Backtesting CLI Engine")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run a walk-forward backtest")
    run_parser.add_argument("--window", type=str, default="30d", help="Window duration (e.g. 30d, 60d, 90d)")
    run_parser.add_argument("--strategies", type=str, default="all", help="Comma-separated strategy names or 'all'")
    run_parser.add_argument("--quality-mode", type=str, default="on", choices=["on", "off"], help="Quality Mode 'on' or 'off'")
    run_parser.add_argument("--tokens", type=str, default="all", help="Comma-separated tokens list or 'all'")

    # Report command
    report_parser = subparsers.add_parser("report", help="Load and view a backtest report")
    report_parser.add_argument("--run-id", type=str, required=True, help="Backtest run UUID to load")

    # List command
    subparsers.add_parser("list", help="List all saved backtest runs")

    return parser.parse_args()

async def main_async():
    args = parse_args()
    
    # Initialize DB (creates tables if not exists)
    from persistence.db import init_db
    init_db()

    if args.command == "run":
        # 1. Parse window days
        window_str = args.window.lower().replace("d", "")
        try:
            window_days = int(window_str)
        except ValueError:
            print(f"Error: Invalid window format '{args.window}'. Use formats like 30d, 60d, 90d.")
            sys.exit(1)

        # 2. Parse strategies
        if args.strategies.lower() == "all":
            strat_list = list(STRATEGIES.keys())
        else:
            strat_list = [s.strip() for s in args.strategies.split(",") if s.strip()]
            for s in strat_list:
                if s not in STRATEGIES:
                    print(f"Error: Unknown strategy '{s}'. Choose from: {list(STRATEGIES.keys())}")
                    sys.exit(1)

        # 3. Parse quality mode
        quality_mode = (args.quality_mode == "on")

        # 4. Parse tokens
        if args.tokens.lower() == "all":
            tokens_list = [t.symbol for t in iter_tradable()]
        else:
            tokens_list = [t.strip().upper() for t in args.tokens.split(",") if t.strip()]

        print(f"==================================================")
        print(f"XORR v2 Backtest Runner")
        print(f"Window:       {window_days} days")
        print(f"Strategies:   {', '.join(strat_list)}")
        print(f"Quality Mode: {quality_mode}")
        print(f"Tokens:       {', '.join(tokens_list)}")
        print(f"==================================================")

        def cli_progress_callback(pct, trades_count, current_symbol):
            print(f"[BACKTEST PROGRESS] {pct}% complete | Symbol: {current_symbol:<6} | Trades so far: {trades_count}", end="\r")

        report = await run_walk_forward_backtest(
            window_days=window_days,
            strategies=strat_list,
            quality_mode=quality_mode,
            symbols=tokens_list,
            progress_callback=cli_progress_callback
        )
        print("\n[BACKTEST COMPLETE]")
        
        # Save to database
        save_backtest_run(report)
        print(f"Report saved to SQLite database. Run ID: {report.run_id}")
        
        # Output summary metrics
        print(f"\n--- Backtest Summary ---")
        print(f"Total Trades:     {report.total_trades}")
        print(f"Win Rate:         {report.win_rate}%")
        print(f"Expectancy (R):   {report.expectancy_r} R")
        print(f"Profit Factor:    {report.profit_factor}")
        print(f"Total PnL:        {report.total_pnl_pct}%")
        print(f"Max Drawdown:     {report.max_drawdown_pct}%")
        print(f"Sharpe Ratio:     {report.sharpe}")
        print(f"------------------------\n")

        # Per-strategy breakdown (ranked by expectancy) — used to pick the best
        if report.by_strategy:
            print("--- By Strategy (ranked by expectancy R) ---")
            print(f"{'strategy':<20} | {'trades':>6} | {'win%':>6} | {'exp R':>6} | {'pnl $':>8}")
            print("-" * 60)
            for name, s in sorted(report.by_strategy.items(), key=lambda kv: kv[1].expectancy_r, reverse=True):
                print(f"{name:<20} | {s.trades:>6} | {s.win_rate:>6} | {s.expectancy_r:>6} | {s.pnl_usd:>8}")
            print()

        # Top / bottom symbols by PnL
        if report.by_symbol:
            ranked = sorted(report.by_symbol.items(), key=lambda kv: kv[1].pnl_usd, reverse=True)
            print("--- Top 8 symbols by PnL ---")
            for sym, s in ranked[:8]:
                print(f"  {sym:<10} trades={s.trades:<3} win%={s.win_rate:<6} pnl=${s.pnl_usd}")
            if len(ranked) > 8:
                print("--- Bottom 5 symbols by PnL ---")
                for sym, s in ranked[-5:]:
                    print(f"  {sym:<10} trades={s.trades:<3} win%={s.win_rate:<6} pnl=${s.pnl_usd}")
            print()
        
    elif args.command == "report":
        report = load_backtest_run(args.run_id)
        if not report:
            print(f"Error: Backtest run '{args.run_id}' not found.")
            sys.exit(1)
            
        print(json.dumps(report.__dict__, indent=2, default=str))
        
    elif args.command == "list":
        runs = list_backtest_runs()
        if not runs:
            print("No backtest runs found in database.")
            return
            
        print(f"{'Run ID':<40} | {'Window':<6} | {'QM':<5} | {'Trades':<6} | {'WR %':<6} | {'Expect':<6} | {'PnL %':<7} | {'MaxDD %':<7}")
        print("-" * 105)
        for r in runs:
            print(f"{r['run_id']:<40} | {r['window_days']:<6} | {str(r['quality_mode']):<5} | {r['total_trades']:<6} | {r['win_rate']:<6} | {r['expectancy_r']:<6} | {r['total_pnl_pct']:<7} | {r['max_drawdown_pct']:<7}")
    else:
        print("Error: Specify a command (run, report, list). Run with --help for details.")

def main():
    asyncio.run(main_async())

if __name__ == "__main__":
    main()
