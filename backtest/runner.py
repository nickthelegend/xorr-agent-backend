import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
import uuid

from backtest.engine import BacktestEngine, BacktestTrade
from backtest.reporter import compile_report, BacktestReport
from data.tokens import iter_tradable
from config import settings

logger = logging.getLogger("xorr.backtest.runner")

async def run_walk_forward_backtest(
    window_days: int,
    strategies: List[str],
    quality_mode: bool,
    symbols: Optional[List[str]] = None,
    progress_callback = None
) -> BacktestReport:
    """
    Orchestrates walk-forward validation.
    Splits the timeframe into 30-day segments:
    - Each segment has a 7-day warm-up and 23-day trading period.
    - The final cash/equity carries over to the next segment.
    """
    if not symbols:
        symbols = [t.symbol for t in iter_tradable()]
        
    now = datetime.now(timezone.utc)
    start_time = now - timedelta(days=window_days)
    
    # Split timeframe into 30-day segments
    segments = []
    segment_duration = timedelta(days=30)
    
    current_start = start_time
    while current_start < now:
        current_end = min(current_start + segment_duration, now)
        if (current_end - current_start).days >= 10:  # minimum segment length
            segments.append((current_start, current_end))
        current_start = current_end
        
    if not segments:
        # Fallback to a single window if days is very short
        segments = [(start_time, now)]
        
    all_trades: List[BacktestTrade] = []
    current_cash = 100.0  # Start with $100 simulated
    
    total_segments = len(segments)
    logger.info(f"[RUNNER] Running {total_segments} walk-forward segments for {window_days} days total")
    
    # We will track equity curve data points
    equity_curve: List[Dict[str, Any]] = []
    
    for i, (seg_start, seg_end) in enumerate(segments):
        # 7 days warm-up
        warmup_days = 7
        if (seg_end - seg_start).days <= 10:
            warmup_days = 1  # reduce warmup for tiny test windows
            
        trading_start = seg_start + timedelta(days=warmup_days)
        
        logger.info(f"[RUNNER] Segment {i+1}/{total_segments}: {seg_start.strftime('%Y-%m-%d')} to {seg_end.strftime('%Y-%m-%d')} (Trading starts {trading_start.strftime('%Y-%m-%d')})")
        
        # Segment progress helper
        def seg_progress_callback(pct, trades_so_far, current_symbol):
            if progress_callback:
                # Calculate global percentage
                global_pct = int(((i * 100) + pct) / total_segments)
                progress_callback(global_pct, len(all_trades) + trades_so_far, current_symbol)

        engine = BacktestEngine(
            symbols=symbols,
            start_time=seg_start,
            end_time=seg_end,
            trading_start_time=trading_start,
            quality_mode=quality_mode,
            confluence_threshold=settings.confluence_threshold if quality_mode else settings.confluence_threshold_relaxed,
            enabled_strategies=strategies,
            base_trade_size=settings.base_trade_size_usd,
            progress_callback=seg_progress_callback
        )
        
        engine.cash = current_cash
        engine.current_equity = current_cash
        engine.peak_equity = current_cash
        
        # Load historical candles
        await engine.load_data()
        
        # Run segment
        seg_trades = await engine.run()
        
        # Close any positions still open at the end of the segment
        # valued at their final price
        for pos in engine.open_positions:
            if pos.is_shadow:
                continue
            # Look up final close price
            final_price = pos.entry_price # fallback
            df = engine.data_5m.get(pos.symbol)
            if df is not None and not df.empty:
                final_price = float(df.iloc[-1]["close"])
                
            exit_usd = pos.size * final_price * 0.995 * (1.0 - 0.0025)
            pnl_usd = exit_usd - pos.invested
            pnl_pct = (pnl_usd / pos.invested) * 100.0 if pos.invested > 0 else 0.0
            
            # Risk/R realized calculation
            sl_diff = abs(pos.entry_price - pos.stop_loss)
            initial_risk = pos.invested * (sl_diff / pos.entry_price)
            r_val = pnl_usd / initial_risk if initial_risk > 0 else 0.0
            
            trade = BacktestTrade(
                id=pos.id,
                symbol=pos.symbol,
                strategy=pos.strategy,
                opened_at=datetime.fromtimestamp(pos.entry_time_ms / 1000.0, timezone.utc),
                closed_at=seg_end,
                invested=pos.invested,
                pnl_usd=round(pnl_usd, 4),
                pnl_pct=round(pnl_pct, 2),
                hold_minutes=round((seg_end.timestamp() * 1000 - pos.entry_time_ms) / 60000.0, 1),
                exit_reason="SEGMENT_END_FORCE_CLOSE",
                status="win" if pnl_usd > 0 else "loss",
                r_realized=round(r_val, 2)
            )
            seg_trades.append(trade)
            engine.cash += exit_usd
            
        all_trades.extend(seg_trades)
        current_cash = engine.cash
        
        # Append to equity curve
        equity_curve.append({
            "t": seg_end.isoformat(),
            "equity": round(current_cash, 2)
        })
        
    # Generate final report
    report = compile_report(
        run_id=str(uuid.uuid4()),
        started_at=start_time,
        ended_at=now,
        window_days=window_days,
        quality_mode=quality_mode,
        trades=all_trades,
        equity_curve=equity_curve
    )
    
    return report
