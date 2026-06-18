import time
from datetime import datetime, timezone
from sqlmodel import Session, select
from persistence.models import Position, Trade, EquityPoint
from persistence.repo import get_positions, remove_position, add_trade, get_state, update_state
from core.rpc import get_balance_of
from core.wallet import WalletManager
from api.stream import log_engine_msg

async def reconcile_on_boot(session: Session, wallet_mgr: WalletManager):
    """
    Startup Reconciler.
    Syncs DB active positions with real on-chain balances. If a token is sold manually,
    the position is closed in the local DB to keep accounting correct.
    """
    await log_engine_msg(session, "info", "[BOOT] Reconciler loop starting...")
    
    positions = get_positions(session)
    wallet_address = await wallet_mgr.get_address()
    executor = wallet_mgr.executor
    
    reconciled_count = 0
    now_dt = datetime.now(timezone.utc)

    # On-chain perp positions (live only). None => unverifiable: never touch a
    # local perp on uncertain data (fail-safe).
    onchain_perp_syms = None
    try:
        perp_list = await executor.list_perp_positions()
        if perp_list is not None:
            onchain_perp_syms = {str(p.get("symbol") or p.get("asset") or "").upper() for p in perp_list}
    except Exception as e:
        print(f"[RECONCILER] perp position read failed: {e}")
        onchain_perp_syms = None

    for pos in positions:
        # --- PERP reconciliation: by the on-chain perp list, NOT token balance
        #     (a perp holds no underlying token, so the spot check below would
        #     falsely flag every perp as a manual sale). ---
        if getattr(pos, "is_perp", False):
            if executor.simulation or onchain_perp_syms is None:
                continue  # sim = local authoritative; live-unverifiable = leave it
            if pos.symbol.upper() not in onchain_perp_syms:
                await log_engine_msg(session, "warn", f"[RECONCILER] Perp {pos.symbol} {getattr(pos,'direction','long')} not found on-chain (closed/liquidated externally). Closing local record.")
                trade = session.get(Trade, pos.id)
                if trade:
                    trade.status = "loss"
                    trade.closed_at = now_dt.isoformat()
                    trade.exit_reason = "RECONCILE_CLOSED"
                    session.add(trade)
                remove_position(session, pos.id)
                reconciled_count += 1
            continue

        # Fetch current on-chain balance of this token (SPOT)
        real_balance = 0.0
        try:
            if executor.simulation:
                # Paper positions are authoritative in simulation; the ledger sums
                # open positions so this always matches (no false manual-sale).
                real_balance = float(await executor.get_balance(pos.contract))
            else:
                real_balance = get_balance_of(pos.contract, wallet_address)
        except Exception as e:
            print(f"[RECONCILER WARNING] Could not retrieve on-chain balance for {pos.symbol}: {e}")
            continue
            
        # If real balance is less than 50% of our recorded position sizing,
        # we assume the operator sold the position manually on-chain
        if real_balance < (pos.size * 0.50):
            await log_engine_msg(
                session, 
                "warn", 
                f"[RECONCILER] Manual sale detected for {pos.symbol}. Actual on-chain={real_balance}, DB={pos.size}. Closing local position."
            )
            
            # Close trade as loss/manual exit
            trade = session.get(Trade, pos.id)
            if trade:
                trade.status = "loss"
                trade.closed_at = now_dt.isoformat()
                trade.exit_reason = "MANUAL_CLOSE"
                session.add(trade)
            else:
                new_trade = Trade(
                    id=pos.id,
                    opened_at=datetime.fromtimestamp(pos.opened_at, timezone.utc).isoformat(),
                    closed_at=now_dt.isoformat(),
                    symbol=pos.symbol,
                    contract=pos.contract,
                    status="loss",
                    invested=pos.invested,
                    pnl_usd=0.0,
                    pnl_pct=0.0,
                    hold_minutes=0.0,
                    exit_reason="MANUAL_CLOSE",
                    window="COMPETITION",
                    tx_open=pos.id,
                    tx_close="MANUAL",
                    strategy=pos.strategy
                )
                session.add(new_trade)
                
            # Remove from open positions
            remove_position(session, pos.id)
            reconciled_count += 1

    # Recompute total equity and write EquityPoint
    wallet_state = await wallet_mgr.get_state()
    usdt_bal = 0.0
    bnb_bal = 0.0
    for b in wallet_state["balances"]:
        if b["symbol"] == "USDT":
            usdt_bal = b["amount"]
        elif b["symbol"] == "BNB":
            bnb_bal = b["amount"]

    bnb_price = 600.0
    bnb_usd_val = bnb_bal * bnb_price
    total_portfolio_usd = usdt_bal + bnb_usd_val
    
    # Add open position value
    active_pos = get_positions(session)
    for pos in active_pos:
        # We will assume entry price or current price
        # For reconciler boot, entry price is standard
        total_portfolio_usd += pos.invested

    # Write new EquityPoint
    pt = EquityPoint(t=now_dt.isoformat(), equity_usd=total_portfolio_usd)
    session.add(pt)
    
    # Initialize peak equity in database if not set
    state = get_state(session)
    if state.peak_equity <= 0.0 or total_portfolio_usd > state.peak_equity:
        update_state(session, peak_equity=total_portfolio_usd)

    # Capture the starting-equity baseline once, for accurate return% reporting
    state = get_state(session)
    if state.start_equity <= 0.0 and total_portfolio_usd > 0.0:
        update_state(session, start_equity=total_portfolio_usd)
        
    session.commit()
    await log_engine_msg(session, "info", f"[BOOT] Reconciled {reconciled_count} positions. Initialized peak equity to ${total_portfolio_usd:.2f}")
