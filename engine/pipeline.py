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
from strategies.registry import active_strategies, shadow_test_names, STRATEGIES
from brain.council import score_council
from brain.decision_log import log_decision
from risk.drawdown_ladder import calculate_drawdown_multiplier
from risk.sizing import calculate_trade_size, calculate_perp_margin, calculate_claude_size
from core import perp_math
from risk.kill_switch import check_kill_switch
from risk.qualifier import is_qualifier_trade_needed, execute_qualifier_trade
from api.stream import log_engine_msg, tick_equity_val
from persistence.models import Position, Trade, RuntimeState
from persistence.repo import add_position, add_trade, get_state
from strategies.arbiter import arbiter

def enforce_spot_only(signals: list) -> list:
    """Spot-only guard: drop SHORT signals (can't short spot) and force every
    remaining signal to a 1x spot long. Idempotent — spot signals pass through
    unchanged. The single chokepoint guaranteeing no short/leveraged order can
    reach execution in the spot-only competition."""
    out = []
    for s in signals:
        if getattr(s, "direction", "long") == "short":
            continue
        s.venue = "spot"
        s.leverage = 1.0
        s.direction = "long"
        out.append(s)
    return out


def _perp_universe(ctx: MarketContext) -> list:
    """Liquid majors we trade as perps: the configured perp_symbols that (a) have
    a live quote this tick and (b) resolve to a known/eligible token. The strategy
    self-checks membership too, so this is just the candidate feed."""
    raw = getattr(settings, "perp_symbols", "") or ""
    wanted = [s.strip().upper() for s in raw.split(",") if s.strip()]
    out = []
    for sym in wanted:
        if sym not in ctx.quotes:
            continue
        out.append(sym)
    return out


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

    # 2b. Live macro signals from the CMC skills marketplace (fail-open)
    macro = {}
    try:
        from data.cmc_signals import get_macro_signals
        macro = await get_macro_signals()
    except Exception as e:
        print(f"[MACRO] CMC macro signals unavailable: {e}")
    
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
        regime=regime,
        macro=macro
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
    # Shadow positions are paper (no capital) — exclude them from real equity,
    # slot counts, sizing and the kill switch. They're managed by the monitor.
    real_open = [p for p in open_pos if not getattr(p, "is_shadow", False)]
    portfolio_value = usdt_balance + (bnb_balance * ctx.bnb_price_usd)
    for pos in real_open:
        # Perp-aware valuation: spot = units*price; perp = margin + directional uPnL.
        quote = ctx.quotes.get(pos.symbol.upper())
        price = quote.price if quote else 0.0
        portfolio_value += perp_math.position_equity(pos, price)
            
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

    # 4b. Risk pause: after a soft de-risk flatten, skip NEW entries until the
    #     pause window expires. The daily qualifier (above) and the monitor loop
    #     keep running, so the min-1-trade/day rule is still met while we cool off.
    if time.time() < (get_state(session).risk_paused_until or 0.0):
        await log_engine_msg(session, "warn", "[bot] Risk pause active (post de-risk flatten). Skipping new entries this scan.")
        return

    # 4c. Circuit breaker: never OPEN trades on a stale/frozen price feed (a dead
    #     data source returning the cached book is how agents trade into ghosts).
    if ctx.quotes:
        newest = max((q.last_updated for q in ctx.quotes.values()), default=None)
        if newest is not None:
            age = (datetime.now(timezone.utc) - newest).total_seconds()
            if age > settings.data_max_staleness_sec:
                await log_engine_msg(session, "warn", f"[bot] Price feed stale ({age:.0f}s old > {settings.data_max_staleness_sec}s). Skipping entries; monitor still manages open positions.")
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
    enabled_strats = active_strategies(settings, list(arbiter.suspended_strategies), list(arbiter.promoted_strategies))
    perp_strats = [s for s in enabled_strats if "perp" in s.name.lower()]
    spot_strats = [s for s in enabled_strats if "perp" not in s.name.lower()]

    # 7a. SPOT strategies on the long-biased candidate set
    for token, quote in candidates:
        symbol = token.symbol
        candles_5m = await fetch_binance_klines(symbol, "5m", limit=35)
        candles_1h = await fetch_binance_klines(symbol, "1h", limit=80)
        for strat in spot_strats:
            if not is_actionable(ctx.regime, strat.name):
                continue
            try:
                sig = await strat.evaluate(symbol, candles_5m, candles_1h, ctx)
                if sig:
                    raw_signals.append(sig)
            except Exception as e:
                print(f"[ENGINE ERROR] Strategy '{strat.name}' crashed evaluating {symbol}: {e}")

    # 7b-spot. SPOT-ONLY (competition): the "*_perp" reversion strategies are our
    #     spot mean-reversion edge. Run them on the liquid majors but take only the
    #     LONG side (can't short spot) and execute as 1x spot swaps — no TWAK, no
    #     leverage, no liquidation risk, no funding-fade. Longs fire on down-flushes
    #     in TREND_UP/CHOP; in a falling tape they stay flat (capital preservation).
    spot_only = bool(getattr(settings, "spot_only", False))
    if spot_only and perp_strats:
        # Strategies whose edge is the SHORT side don't belong in a long-only spot
        # book — keep them enabled for perps, but skip them here.
        _spot_excl = {s.strip() for s in (getattr(settings, "spot_excluded_strategies", "") or "").split(",") if s.strip()}
        spot_reversion = [s for s in perp_strats if s.name not in _spot_excl]
        for symbol in _perp_universe(ctx):
            quote = ctx.quotes.get(symbol.upper())
            if not quote or is_blacklisted(session, symbol):
                continue
            if not await passes_cex_sanity(symbol, quote.price):
                continue
            candles_5m = await fetch_binance_klines(symbol, "5m", limit=35)
            candles_1h = await fetch_binance_klines(symbol, "1h", limit=80)
            for strat in spot_reversion:
                try:
                    sig = await strat.evaluate(symbol, candles_5m, candles_1h, ctx)
                except Exception as e:
                    print(f"[ENGINE ERROR] Reversion strategy '{strat.name}' crashed on {symbol}: {e}")
                    continue
                if not sig or getattr(sig, "direction", "long") != "long":
                    continue   # spot can't short — only the buy-the-flush side
                sig.venue = "spot"; sig.leverage = 1.0; sig.direction = "long"
                raw_signals.append(sig)

    # 7b. PERP book: evaluate the liquid majors DIRECTLY, bypassing the long-biased
    #     candidate gates (whale-flow / confluence) so a breakdown SHORT in a falling
    #     tape is actually seen. CEX-sanity + cooldown still apply.
    shadow_perp = [n for n in shadow_test_names(settings, [s.name for s in enabled_strats]) if "perp" in n]
    # Perps can only EXECUTE in sim (paper) or live-with-TWAK. If we're live without
    # TWAK creds, skip generating real perp signals (they'd fail at open_perp) — the
    # spot book still trades on-chain via the web3 keystore. Shadow perps are paper,
    # so they keep running regardless.
    perps_live_ok = executor.simulation or executor._twak_ready()
    if not perps_live_ok:
        perp_strats = []
    if (not spot_only) and settings.enable_perps and (perp_strats or shadow_perp):
        existing_shadow = {(p.strategy, p.symbol.upper()) for p in open_pos if getattr(p, "is_shadow", False)}
        n_shadow = sum(1 for p in open_pos if getattr(p, "is_shadow", False))
        for symbol in _perp_universe(ctx):
            quote = ctx.quotes.get(symbol.upper())
            if not quote:
                continue
            if is_blacklisted(session, symbol):
                continue
            if not await passes_cex_sanity(symbol, quote.price):
                continue
            candles_5m = await fetch_binance_klines(symbol, "5m", limit=35)
            candles_1h = await fetch_binance_klines(symbol, "1h", limit=80)
            # --- real perp signals (funding-fade biased) ---
            for strat in perp_strats:
                try:
                    sig = await strat.evaluate(symbol, candles_5m, candles_1h, ctx)
                    if not sig:
                        continue
                    if getattr(settings, "enable_funding_fade", True):
                        try:
                            from data.funding import get_funding_state, funding_confidence_mult
                            fstate = await get_funding_state(symbol)
                            mult, reason = funding_confidence_mult(sig.direction, fstate)
                            if mult != 1.0:
                                sig.confidence = max(0.05, min(0.97, sig.confidence * mult))
                                sig.rationale += f" | {reason}"
                        except Exception as fe:
                            print(f"[FUNDING] bias skipped for {symbol}: {fe}")
                    raw_signals.append(sig)
                except Exception as e:
                    print(f"[ENGINE ERROR] Perp strategy '{strat.name}' crashed on {symbol}: {e}")
            # --- SHADOW paper positions for disabled strategies (no capital) ---
            for name in shadow_perp:
                if n_shadow >= 12:
                    break
                if (f"shadow_{name}", symbol.upper()) in existing_shadow:
                    continue
                try:
                    ssig = await STRATEGIES[name]().evaluate(symbol, candles_5m, candles_1h, ctx)
                except Exception:
                    ssig = None
                if not ssig:
                    continue
                lev = float(getattr(ssig, "leverage", settings.perp_leverage))
                entry = quote.price
                if ssig.direction == "short":
                    s_sl = entry * (1 + ssig.stop_loss_pct / 100.0); s_tp = entry * (1 - ssig.take_profit_pct / 100.0)
                else:
                    s_sl = entry * (1 - ssig.stop_loss_pct / 100.0); s_tp = entry * (1 + ssig.take_profit_pct / 100.0)
                spos = Position(
                    id=f"SHADOW:{uuid.uuid4()}", symbol=symbol, contract=ssig.contract, opened_at=time.time(),
                    entry_price=entry, stop_loss=s_sl, take_profit=s_tp, init_stop=s_sl,
                    size=perp_math.notional_units(1.0, lev, entry), strategy=f"shadow_{name}", invested=1.0,
                    mode="simulation" if executor.simulation else "live", is_perp=True, venue="perp",
                    direction=ssig.direction, leverage=lev, margin_usd=1.0,
                    liquidation_price=perp_math.liquidation_price(entry, lev, ssig.direction), is_shadow=True,
                )
                add_position(session, spos)
                existing_shadow.add((f"shadow_{name}", symbol.upper())); n_shadow += 1
                await log_engine_msg(session, "info", f"[shadow] {name} {ssig.direction} {symbol} paper-opened @ ${entry:.4f}")

    # Spot-only safety net: guarantee nothing short or leveraged reaches execution,
    # regardless of which strategy produced the signal.
    if spot_only:
        raw_signals = enforce_spot_only(raw_signals)

    # Claude decision brain (subscription CLI): Claude sets the watchlist + entry alerts
    # every 4h; here we check which alerts the LIVE price just triggered and enter those
    # (deterministic — no per-tick Claude call). Force-entered below (Claude decides, not Groq).
    if bool(getattr(settings, "enable_claude_brain", False)):
        try:
            from claude.playbook import triggered_signals as _claude_triggers
            cs = _claude_triggers(ctx)
            if cs:
                raw_signals.extend(cs)
                await log_engine_msg(session, "info", f"[claude] {len(cs)} alert(s) triggered this scan -> entering.")
        except Exception as e:
            print(f"[CLAUDE] alert check skipped: {e}")

    if not raw_signals:
        await log_engine_msg(session, "info", "[bot] No technical signals triggered. Scan complete.")
        return

    # 7b. Combine multi-strategy agreement into high-conviction ensemble signals
    from strategies.combiner import combine_signals
    combined_signals = combine_signals(raw_signals)
    n_combos = sum(1 for s in combined_signals if s.n_agree > 1)
    if n_combos:
        await log_engine_msg(session, "info", f"[bot] {n_combos} ensemble signal(s) where multiple strategies agree.")

    # 8. Post-Process via Arbiter
    arbitrated_signals = arbiter.filter(session, combined_signals, real_open)
    if not arbitrated_signals:
        await log_engine_msg(session, "info", "[bot] All signals rejected by Strategy Arbiter.")
        return

    # 9. Score and rank via LLM Brain Council
    await log_engine_msg(session, "info", f"[bot] Sending {len(arbitrated_signals)} signals to LLM Brain Council for scoring...")
    council_min = settings.sim_council_min if executor.simulation else settings.council_min_final_confidence
    decisions = await score_council(arbitrated_signals, ctx, min_conf=council_min)

    # 10. Execute highest ranked trade(s)
    # We only take the top scored signals up to remaining concurrent slots
    free_slots = settings.max_concurrent_positions - len(real_open)
    if free_slots <= 0:
        await log_engine_msg(session, "info", "[bot] Standard scan complete. Portfolio fully occupied (max concurrent positions).")
        return
        
    trades_executed = 0
    from dataclasses import asdict
    for sig, dec in zip(arbitrated_signals, decisions):
        # Claude's playbook picks ARE the decision — force-enter them (the Groq council
        # doesn't get a veto on what Claude already chose).
        if str(getattr(sig, "strategy_name", "")).startswith("claude:"):
            dec.action = "enter"
            dec.final_confidence = max(getattr(dec, "final_confidence", 0.0), float(sig.confidence))
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

        is_perp_sig = getattr(sig, "venue", "spot") == "perp"
        direction = getattr(sig, "direction", "long")
        leverage = float(getattr(sig, "leverage", 1.0)) if is_perp_sig else 1.0

        # Calculate risk sizing — perps size by MARGIN under hard caps; spot by notional.
        is_claude_pick = str(getattr(sig, "strategy_name", "")).startswith("claude:")
        if is_perp_sig:
            stake = calculate_perp_margin(
                equity=portfolio_value,
                open_positions=real_open,
                council_confidence=dec.final_confidence,
                n_agree=getattr(sig, "n_agree", 1),
                drawdown_multiplier=dd_mult,
                available_usdt=usdt_balance,
            )
        elif is_claude_pick:
            # Claude's deliberate, confluence-verified pick — concentrate real capital by
            # conviction, capped to protect the DQ gate; no Fear&Greed penalty (fading fear).
            deployed_usd = sum(float(getattr(p, "invested", 0.0) or 0.0)
                               for p in real_open if getattr(p, "venue", "spot") != "perp")
            stake = calculate_claude_size(
                available_usdt=usdt_balance, deployed_usd=deployed_usd,
                active_position_count=len(real_open),
                conviction=dec.final_confidence, drawdown_multiplier=dd_mult,
            )
        else:
            stake = calculate_trade_size(
                ctx.fear_greed_value, dd_mult, sig.strategy_name, usdt_balance, len(real_open),
                council_confidence=dec.final_confidence, council_consensus=dec.consensus,
                n_agree=getattr(sig, "n_agree", 1),
            )
        if stake <= 0.0:
            log_decision(DecisionLog(
                id=dec.decision_id, t=now_dt, symbol=sig.symbol, action="SKIP",
                strategy=sig.strategy_name, brain_score=score,
                reasoning=f"Sizing rejected (size=$0). perp={is_perp_sig}, F&G={ctx.fear_greed_value}, DD={dd_mult}",
                council=asdict(dec)))
            continue

        venue_lbl = f"{direction.upper()} {leverage:.0f}x PERP" if is_perp_sig else "SPOT"
        await log_engine_msg(session, "warn", f"[bot] ENTERING {venue_lbl} {sig.symbol} via {sig.strategy_name} for ${stake:.2f}...")

        quote = ctx.quotes.get(sig.symbol.upper())
        token_price = quote.price if quote else 1.0
        slippage = settings.slippage_bps_spot / 10000.0
        if "news" in sig.strategy_name.lower():
            slippage = settings.slippage_bps_news / 10000.0

        try:
            if is_perp_sig:
                res = await executor.open_perp(
                    symbol=sig.symbol, direction=direction, margin_usd=Decimal(str(stake)),
                    leverage=leverage, ref_price=token_price, reason=f"PERP_{sig.strategy_name}",
                )
            else:
                est_tokens = stake / token_price
                min_out = est_tokens * (1.0 - slippage)
                res = await executor.swap(
                    token_in=settings.usdt_contract, token_out=sig.contract,
                    amount_in=Decimal(str(stake)), min_out=Decimal(str(min_out)),
                    reason=f"ENTRY_{sig.strategy_name}", ref_price=token_price,
                )

            if res.success:
                pos_id = res.tx_hash
                entry = res.executed_price
                # Direction-aware stop / take-profit (price terms).
                if direction == "short":
                    stop_loss = entry * (1 + sig.stop_loss_pct / 100.0)
                    take_profit = entry * (1 - sig.take_profit_pct / 100.0)
                else:
                    stop_loss = entry * (1 - sig.stop_loss_pct / 100.0)
                    take_profit = entry * (1 + sig.take_profit_pct / 100.0)
                liq = perp_math.liquidation_price(entry, leverage, direction) if is_perp_sig else 0.0

                new_pos = Position(
                    id=pos_id, symbol=sig.symbol, contract=sig.contract, opened_at=time.time(),
                    entry_price=entry, stop_loss=stop_loss, take_profit=take_profit,
                    size=res.amount_out, strategy=sig.strategy_name, invested=stake,
                    mode="simulation" if executor.simulation else "live",
                    is_perp=is_perp_sig, venue=("perp" if is_perp_sig else "spot"),
                    direction=direction, leverage=leverage,
                    margin_usd=(stake if is_perp_sig else 0.0), liquidation_price=liq,
                    init_stop=stop_loss,
                )
                add_position(session, new_pos)

                new_trade = Trade(
                    id=pos_id, opened_at=now_dt.isoformat(), closed_at=None, symbol=sig.symbol,
                    contract=sig.contract, status="open", invested=stake, pnl_usd=0.0, pnl_pct=0.0,
                    hold_minutes=0.0, entry_price=entry, exit_price=None,
                    entry_mc=quote.market_cap if quote else 0.0, exit_mc=0.0, score=score,
                    exit_reason=None, window="COMPETITION", tx_open=pos_id, tx_close=None,
                    strategy=sig.strategy_name, direction=direction,
                    venue=("perp" if is_perp_sig else "spot"), leverage=leverage,
                )
                add_trade(session, new_trade)

                usdt_balance -= stake  # margin/notional committed this cycle

                liq_txt = f" liq=${liq:.4f}" if is_perp_sig else ""
                await log_engine_msg(session, "info", f"[bot] POSITION OPENED: {venue_lbl} {sig.symbol} size={res.amount_out:.6f} entry=${entry:.4f}{liq_txt} tx={pos_id}")

                # Telegram alert (fire-and-forget — never stalls the loop)
                try:
                    from core import telegram
                    telegram.fire(telegram.notify_open(
                        symbol=sig.symbol, strategy=sig.strategy_name, size_usd=stake,
                        entry=entry, stop=stop_loss, target=take_profit,
                        conviction=float(getattr(dec, "final_confidence", 0.0)),
                        equity=portfolio_value, open_n=len(real_open) + 1,
                        max_n=settings.max_concurrent_positions,
                        mode=("live" if not executor.simulation else "sim")))
                except Exception:
                    pass

                log_decision(DecisionLog(
                    id=dec.decision_id, t=now_dt, symbol=sig.symbol, action="ENTER",
                    strategy=sig.strategy_name, filters_passed=["cex_sanity", "cooldown"],
                    brain_score=score,
                    reasoning=f"{venue_lbl} entered. Council confidence: {dec.final_confidence:.2f}: {reasoning}",
                    market_snapshot={"price": entry, "score": score}, council=asdict(dec)))

                trades_executed += 1
                real_open.append(new_pos)
            else:
                await log_engine_msg(session, "error", f"[bot] Entry failed for {sig.symbol}: {res.error}")
        except Exception as e:
            await log_engine_msg(session, "error", f"[bot] Exception during entry execution: {e}")
            
    await log_engine_msg(session, "info", "[bot] Pipeline scan complete.")
