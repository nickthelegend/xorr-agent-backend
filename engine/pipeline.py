import time
import uuid
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from sqlmodel import Session
from config import settings
from core.types import MarketContext, Signal, DecisionLog
from core.twak_executor import TwakExecutor
from data.tokens import iter_all, resolve
from data.cmc_client import fetch_cmc_quotes
from data.fear_greed import get_fear_greed
from data.binance_klines import fetch_binance_klines
from filters.vedic_timing import is_favorable
from filters.regime import classify_market_regime, is_actionable
from filters.cex_sanity import passes_cex_sanity
from filters.liquidity_gate import passes_liquidity_gate
from filters.cooldown import is_blacklisted
from strategies.momentum_pullback import MomentumPullbackStrategy
from strategies.fib_golden_pocket import FibGoldenPocketStrategy
from strategies.capitulation import CapitulationStrategy
from strategies.news_catalyst import NewsCatalystStrategy
from strategies.arbiter import arbiter
from brain.llm_client import score_signals
from brain.decision_log import log_decision
from risk.drawdown_ladder import calculate_drawdown_multiplier
from risk.sizing import calculate_trade_size
from risk.kill_switch import check_kill_switch
from risk.qualifier import is_qualifier_trade_needed, execute_qualifier_trade
from api.stream import log_engine_msg, tick_equity_val
from persistence.models import Position, Trade, RuntimeState
from persistence.repo import add_position, add_trade

# Initialize strategy classes
_strategies = [
    MomentumPullbackStrategy(),
    FibGoldenPocketStrategy(),
    CapitulationStrategy(),
    NewsCatalystStrategy()
]

async def build_market_context(session: Session, executor: TwakExecutor) -> MarketContext:
    """Builds the unified MarketContext object by querying data providers and local database."""
    now = datetime.now(timezone.utc)
    
    # 1. Fetch Fear & Greed index
    fng_val = 50
    fng_lbl = "Neutral"
    fng_data = await get_fear_greed()
    if fng_data:
        fng_val = fng_data.get("value", 50)
        fng_lbl = fng_data.get("label", "Neutral")
        
    # 2. Fetch daily BTC klines to get dominance/regime
    regime = await classify_market_regime()
    
    # 3. Get open positions from database
    open_positions = list(session.query(Position).all())
    
    # 4. Fetch latest quotes for all whitelisted tokens
    quotes = await fetch_cmc_quotes()
    
    # Get BNB price
    bnb_price = 600.0
    bnb_quote = quotes.get("BNB")
    if bnb_quote:
        bnb_price = bnb_quote.price
        
    return MarketContext(
        timestamp=now,
        fear_greed_value=fng_val,
        fear_greed_label=fng_lbl,
        btc_dominance=55.0,  # approximate default
        total_market_cap_usd=2.5e12,
        total_market_cap_change_24h=1.5,
        bnb_price_usd=bnb_price,
        quotes=quotes,
        open_positions=open_positions,
        regime=regime
    )

async def run_pipeline_cycle(session: Session, executor: TwakExecutor):
    """Executes a single workflow iteration: fetches data, gates filters, runs strategy signals, scores via LLM, sizes risk, and triggers swaps."""
    await log_engine_msg(session, "info", "[bot] scan tick start")
    
    # 1. Fetch balances
    usdt_balance = float(await executor.get_balance("USDT"))
    bnb_balance = float(await executor.get_balance("BNB"))
    
    # 2. Build market context
    ctx = await build_market_context(session, executor)
    
    # 3. Calculate portfolio equity and evaluate kill switch
    open_pos = ctx.open_positions
    portfolio_value = usdt_balance + (bnb_balance * ctx.bnb_price_usd)
    for pos in open_pos:
        # Get current price of token to calculate current position valuation
        quote = ctx.quotes.get(pos.symbol.upper())
        if quote:
            current_val = pos.size * quote.price
            portfolio_value += current_val
        else:
            portfolio_value += pos.invested  # fallback to entry value if no price
            
    # Tick equity to SSE
    await tick_equity_val(portfolio_value)
    
    # Check emergency kill switch
    if await check_kill_switch(session, portfolio_value, executor):
        await log_engine_msg(session, "error", "[bot] EMERGENCY SHUTDOWN: Kill switch triggered.")
        return

    # 4. Check daily qualifier trade
    if is_qualifier_trade_needed(session):
        await log_engine_msg(session, "info", "[bot] Daily qualifier trade needed. Suspending standard scan to execute.")
        await execute_qualifier_trade(session, executor)
        return

    # 5. Macro filters (Vedic & Regime)
    now_dt = datetime.now(timezone.utc)
    
    # Vedic Timing
    if settings.enable_vedic_filter:
        is_fav, reasons = is_favorable(now_dt)
        if not is_fav:
            await log_engine_msg(session, "warn", f"[bot] Vedic filter blocked cycle. Reasons: {', '.join(reasons)}")
            # Log skips for all whitelist tokens
            for token in iter_all():
                log_decision(DecisionLog(
                    id=str(uuid.uuid4()),
                    t=now_dt,
                    symbol=token.symbol,
                    action="SKIP",
                    strategy="vedic_filter",
                    filters_blocked=["vedic_timing"],
                    reasoning=f"Vedic filter blocked: {', '.join(reasons)}"
                ))
            return
            
    # Macro Regime Check
    if ctx.regime == "RISK_OFF":
        await log_engine_msg(session, "warn", "[bot] Regime is RISK_OFF. Only News Catalyst strategy allowed.")

    # 6. Filter candidate universe
    candidates = []
    tokens = iter_all()
    
    for token in tokens:
        symbol = token.symbol
        quote = ctx.quotes.get(symbol.upper())
        if not quote:
            continue
            
        filters_passed = []
        filters_blocked = []
        
        # CEX Sanity
        if not await passes_cex_sanity(symbol, quote.price):
            filters_blocked.append("cex_sanity")
        else:
            filters_passed.append("cex_sanity")
            
        # Cooldown
        if is_blacklisted(session, symbol):
            filters_blocked.append("cooldown")
        else:
            filters_passed.append("cooldown")
            
        # Liquidity Gate
        if "cex_sanity" in filters_passed and "cooldown" in filters_passed:
            if not await passes_liquidity_gate(executor, symbol, quote.price):
                filters_blocked.append("liquidity_gate")
            else:
                filters_passed.append("liquidity_gate")
                
        # If any filter blocked, record a skip decision
        if filters_blocked:
            log_decision(DecisionLog(
                id=str(uuid.uuid4()),
                t=now_dt,
                symbol=symbol,
                action="SKIP",
                strategy="macro_filters",
                filters_passed=filters_passed,
                filters_blocked=filters_blocked,
                reasoning=f"Filtered out by: {', '.join(filters_blocked)}",
                market_snapshot={"price": quote.price, "change24h": quote.pct_24h}
            ))
            continue
            
        candidates.append((token, quote))

    # 7. Evaluate Strategy Signals
    raw_signals = []
    for token, quote in candidates:
        symbol = token.symbol
        
        # Fetch 5m and 1h candles
        candles_5m = await fetch_binance_klines(symbol, "5m", limit=30)
        candles_1h = await fetch_binance_klines(symbol, "1h", limit=30)
        
        for strat in _strategies:
            # Check strategy-regime actionability
            if not is_actionable(ctx.regime, strat.name):
                continue
                
            try:
                sig = strat.evaluate(symbol, candles_5m, candles_1h, ctx)
                if sig:
                    raw_signals.append(sig)
            except Exception as e:
                print(f"[ENGINE ERROR] Strategy '{strat.name}' crashed evaluating {symbol}: {e}")

    if not raw_signals:
        await log_engine_msg(session, "info", "[bot] No technical signals triggered. Scan complete.")
        return

    # 8. Post-Process via Arbiter
    arbitrated_signals = arbiter.filter(session, raw_signals, open_pos)
    if not arbitrated_signals:
        await log_engine_msg(session, "info", "[bot] All signals rejected by Strategy Arbiter.")
        return

    # 9. Score and rank via Llama Brain
    await log_engine_msg(session, "info", f"[bot] Sending {len(arbitrated_signals)} signals to LLM Brain for scoring...")
    scored_signals = await score_signals(arbitrated_signals, ctx)

    # 10. Execute highest ranked trade(s)
    # We only take the top scored signals up to remaining concurrent slots
    free_slots = settings.max_concurrent_positions - len(open_pos)
    if free_slots <= 0:
        await log_engine_msg(session, "info", "[bot] Standard scan complete. Portfolio fully occupied (max concurrent positions).")
        return
        
    trades_executed = 0
    for ss in scored_signals:
        if trades_executed >= free_slots:
            break
            
        sig = ss.signal
        score = ss.score
        reasoning = ss.reasoning
        
        # Check Brain threshold
        if score < 70.0:
            await log_engine_msg(session, "info", f"[bot] Skipping signal {sig.symbol} ({sig.strategy_name}): LLM score {score:.1f} is below entry threshold (70.0).")
            log_decision(DecisionLog(
                id=str(uuid.uuid4()),
                t=now_dt,
                symbol=sig.symbol,
                action="SKIP",
                strategy=sig.strategy_name,
                brain_score=score,
                reasoning=f"LLM score {score:.1f} rejected: {reasoning}"
            ))
            continue
            
        # Drawdown multiplier
        dd_mult = calculate_drawdown_multiplier(session, portfolio_value)
        
        # Calculate risk sizing
        size_usd = calculate_trade_size(ctx.fear_greed_value, dd_mult, sig.strategy_name, usdt_balance, len(open_pos))
        if size_usd <= 0.0:
            log_decision(DecisionLog(
                id=str(uuid.uuid4()),
                t=now_dt,
                symbol=sig.symbol,
                action="SKIP",
                strategy=sig.strategy_name,
                brain_score=score,
                reasoning=f"Sizing rejected (size=$0). Context: F&G={ctx.fear_greed_value}, DD={dd_mult}"
            ))
            continue
            
        # Execute Entry Swap
        await log_engine_msg(session, "warn", f"[bot] ENTERING position {sig.symbol} via {sig.strategy_name} for ${size_usd:.2f}...")
        
        # Estimate output tokens (slippage protection)
        quote = ctx.quotes.get(sig.symbol.upper())
        token_price = quote.price if quote else 1.0
        slippage = settings.slippage_bps_spot / 10000.0
        if "news" in sig.strategy_name.lower():
            slippage = settings.slippage_bps_news / 10000.0
            
        est_tokens = size_usd / token_price
        min_out = est_tokens * (1.0 - slippage)
        
        try:
            res = await executor.swap(
                token_in=settings.usdt_contract,
                token_out=sig.contract,
                amount_in=Decimal(str(size_usd)),
                min_out=Decimal(str(min_out)),
                reason=f"ENTRY_{sig.strategy_name}"
            )
            
            if res.success:
                # 1. Record position in SQLite
                pos_id = res.tx_hash
                new_pos = Position(
                    id=pos_id,
                    symbol=sig.symbol,
                    contract=sig.contract,
                    opened_at=time.time(),
                    entry_price=res.executed_price,
                    stop_loss=res.executed_price * (1 - sig.stop_loss_pct/100.0),
                    take_profit=res.executed_price * (1 + sig.take_profit_pct/100.0),
                    size=res.amount_out,
                    strategy=sig.strategy_name,
                    invested=size_usd,
                    mode="simulation" if executor.simulation else "live"
                )
                add_position(session, new_pos)
                
                # 2. Record trade in SQLite as open
                new_trade = Trade(
                    id=pos_id,
                    opened_at=now_dt.isoformat(),
                    closed_at=None,
                    symbol=sig.symbol,
                    contract=sig.contract,
                    status="open",
                    invested=size_usd,
                    pnl_usd=0.0,
                    pnl_pct=0.0,
                    hold_minutes=0.0,
                    entry_mc=quote.market_cap if quote else 0.0,
                    exit_mc=0.0,
                    score=score,
                    exit_reason=None,
                    window="COMPETITION",
                    tx_open=pos_id,
                    tx_close=None,
                    strategy=sig.strategy_name
                )
                add_trade(session, new_trade)
                
                # Deduct simulated balance
                if executor.simulation:
                    # Deducted automatically inside executor.swap, but update usdt balance
                    usdt_balance -= size_usd
                
                await log_engine_msg(session, "info", f"[bot] POSITION OPENED: {sig.symbol} units={res.amount_out} price=${res.executed_price:.4f} tx={pos_id}")
                
                # 3. Log brain decision
                log_decision(DecisionLog(
                    id=str(uuid.uuid4()),
                    t=now_dt,
                    symbol=sig.symbol,
                    action="ENTER",
                    strategy=sig.strategy_name,
                    filters_passed=["cex_sanity", "cooldown", "liquidity_gate"],
                    brain_score=score,
                    reasoning=f"Position entered successfully: {reasoning}",
                    market_snapshot={"price": res.executed_price, "score": score}
                ))
                
                trades_executed += 1
                # Recalculate open positions list
                open_pos.append(new_pos)
                
            else:
                await log_engine_msg(session, "error", f"[bot] Entry swap failed for {sig.symbol}: {res.error}")
        except Exception as e:
            await log_engine_msg(session, "error", f"[bot] Exception during swap execution: {e}")
            
    await log_engine_msg(session, "info", "[bot] Pipeline scan complete.")
