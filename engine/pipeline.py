import time
import uuid
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from sqlmodel import Session
from config import settings
from core.types import MarketContext, Signal, DecisionLog
from core.twak_executor import TwakExecutor
from data.tokens import iter_all, iter_tradable, resolve
from data.cmc_client import fetch_cmc_quotes
from data.fear_greed import get_fear_greed
from data.binance_klines import fetch_binance_klines
from filters.regime import classify_market_regime, is_actionable
from filters.cex_sanity import passes_cex_sanity
from filters.liquidity_gate import passes_liquidity_gate
from filters.cooldown import is_blacklisted
import filters.volume_gate as volume_gate
import filters.whale_netflow as whale_netflow
import filters.confluence_score as confluence_score
from strategies.registry import active_strategies
from brain.council import score_council
from brain.decision_log import log_decision
from risk.drawdown_ladder import calculate_drawdown_multiplier
from risk.sizing import calculate_trade_size
from risk.kill_switch import check_kill_switch
from risk.qualifier import is_qualifier_trade_needed, execute_qualifier_trade
from api.stream import log_engine_msg, tick_equity_val
from persistence.models import Position, Trade, RuntimeState
from persistence.repo import add_position, add_trade
from strategies.arbiter import arbiter

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

    # 5. Macro filters (Regime check)
    now_dt = datetime.now(timezone.utc)
    if ctx.regime == "RISK_OFF":
        await log_engine_msg(session, "warn", "[bot] Regime is RISK_OFF. Only News Catalyst strategy allowed.")

    # 6. Filter candidate universe (only the liquid/tradable subset; the full
    # 149 list remains the on-chain eligibility whitelist for live swaps)
    candidates = []
    tokens = iter_tradable()
    
    for token in tokens:
        symbol = token.symbol
        quote = ctx.quotes.get(symbol.upper())
        if not quote:
            continue
            
        filters_passed = []
        filters_blocked = []
        
        # 1. Cooldown
        if is_blacklisted(session, symbol):
            filters_blocked.append("cooldown")
        else:
            filters_passed.append("cooldown")
            
        # 2. Volume Gate
        if not filters_blocked:
            if not await volume_gate.passes(ctx, symbol):
                filters_blocked.append("volume")
            else:
                filters_passed.append("volume")
                
        # 3. CEX Sanity
        if not filters_blocked:
            if not await passes_cex_sanity(symbol, quote.price):
                filters_blocked.append("cex_sanity")
            else:
                filters_passed.append("cex_sanity")
                
        # 4. Whale Netflow
        if not filters_blocked:
            if not await whale_netflow.is_bullish(ctx, symbol):
                filters_blocked.append("whale_netflow")
            else:
                filters_passed.append("whale_netflow")
                
        # 5. Confluence Score
        if not filters_blocked:
            if not await confluence_score.passes(ctx, symbol):
                filters_blocked.append("confluence")
            else:
                filters_passed.append("confluence")
                
        # 6. Liquidity Gate (TWAK subprocess) - DO LAST
        if not filters_blocked:
            if not await passes_liquidity_gate(executor, symbol, quote.price):
                filters_blocked.append("liquidity")
            else:
                filters_passed.append("liquidity")
                
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
    enabled_strats = active_strategies(settings, list(arbiter.suspended_strategies))
    for token, quote in candidates:
        symbol = token.symbol
        
        # Fetch 5m and 1h candles
        candles_5m = await fetch_binance_klines(symbol, "5m", limit=35)
        candles_1h = await fetch_binance_klines(symbol, "1h", limit=60)
        
        for strat in enabled_strats:
            # Check strategy-regime actionability
            if not is_actionable(ctx.regime, strat.name):
                continue
                
            try:
                sig = await strat.evaluate(symbol, candles_5m, candles_1h, ctx)
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

    # 9. Score and rank via LLM Brain Council
    await log_engine_msg(session, "info", f"[bot] Sending {len(arbitrated_signals)} signals to LLM Brain Council for scoring...")
    decisions = await score_council(arbitrated_signals, ctx)

    # 10. Execute highest ranked trade(s)
    # We only take the top scored signals up to remaining concurrent slots
    free_slots = settings.max_concurrent_positions - len(open_pos)
    if free_slots <= 0:
        await log_engine_msg(session, "info", "[bot] Standard scan complete. Portfolio fully occupied (max concurrent positions).")
        return
        
    trades_executed = 0
    from dataclasses import asdict
    for sig, dec in zip(arbitrated_signals, decisions):
        if trades_executed >= free_slots:
            break
            
        score = dec.final_confidence * 100.0
        reasoning = dec.votes[0].get("reasoning", "") if dec.votes else "No reasoning"
        
        # Check Brain threshold action
        if dec.action != "enter":
            await log_engine_msg(session, "info", f"[bot] Skipping signal {sig.symbol} ({sig.strategy_name}): council action is skip (confidence {dec.final_confidence:.2f}).")
            log_decision(DecisionLog(
                id=dec.decision_id,
                t=now_dt,
                symbol=sig.symbol,
                action="SKIP",
                strategy=sig.strategy_name,
                brain_score=score,
                reasoning=f"LLM Council skip (confidence {dec.final_confidence:.2f}): {reasoning}",
                council=asdict(dec)
            ))
            continue
            
        # Drawdown multiplier
        dd_mult = calculate_drawdown_multiplier(session, portfolio_value)
        
        # Calculate risk sizing
        size_usd = calculate_trade_size(
            ctx.fear_greed_value,
            dd_mult,
            sig.strategy_name,
            usdt_balance,
            len(open_pos),
            council_confidence=dec.final_confidence,
            council_consensus=dec.consensus
        )
        if size_usd <= 0.0:
            log_decision(DecisionLog(
                id=dec.decision_id,
                t=now_dt,
                symbol=sig.symbol,
                action="SKIP",
                strategy=sig.strategy_name,
                brain_score=score,
                reasoning=f"Sizing rejected (size=$0). Context: F&G={ctx.fear_greed_value}, DD={dd_mult}",
                council=asdict(dec)
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
                reason=f"ENTRY_{sig.strategy_name}",
                ref_price=token_price
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
                
                # Decrement local cash estimate so sizing of further entries in
                # this same cycle reflects the spend (ledger is updated in swap()).
                usdt_balance -= size_usd
                
                await log_engine_msg(session, "info", f"[bot] POSITION OPENED: {sig.symbol} units={res.amount_out} price=${res.executed_price:.4f} tx={pos_id}")
                
                # 3. Log brain decision
                log_decision(DecisionLog(
                    id=dec.decision_id,
                    t=now_dt,
                    symbol=sig.symbol,
                    action="ENTER",
                    strategy=sig.strategy_name,
                    filters_passed=["cex_sanity", "cooldown", "liquidity"],
                    brain_score=score,
                    reasoning=f"Position entered successfully. Council confidence: {dec.final_confidence:.2f}: {reasoning}",
                    market_snapshot={"price": res.executed_price, "score": score},
                    council=asdict(dec)
                ))
                
                trades_executed += 1
                open_pos.append(new_pos)
                
            else:
                await log_engine_msg(session, "error", f"[bot] Entry swap failed for {sig.symbol}: {res.error}")
        except Exception as e:
            await log_engine_msg(session, "error", f"[bot] Exception during swap execution: {e}")
            
    await log_engine_msg(session, "info", "[bot] Pipeline scan complete.")
