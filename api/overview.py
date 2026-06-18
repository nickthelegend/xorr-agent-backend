from fastapi import APIRouter, Depends
from sqlmodel import Session, select
from datetime import datetime, timezone
from api.deps import get_session, settings
from persistence.repo import get_state, get_trades, get_positions, get_equity_history
from persistence.models import Trade, Position, EquityPoint
from core.twak_executor import TwakExecutor
from core.wallet import WalletManager
from data.fear_greed import get_fear_greed

router = APIRouter()

# Share cached executors from api/wallet
from api.wallet import get_wallet_manager

@router.get("/overview")
async def get_overview(session: Session = Depends(get_session)):
    state = get_state(session)
    trades = get_trades(session, "all")
    positions = get_positions(session)
    
    # Wallet balances
    wallet_mgr = get_wallet_manager(session)
    wallet_state = await wallet_mgr.get_state()
    
    usdt_bal = 0.0
    bnb_bal = 0.0
    for b in wallet_state["balances"]:
        if b["symbol"] == "USDT":
            usdt_bal = b["amount"]
        elif b["symbol"] == "BNB":
            bnb_bal = b["amount"]

    bnb_price = 600.0  # reference price
    bnb_usd_val = bnb_bal * bnb_price

    # Value open positions at current market price so deployed capital is counted
    from data.cmc_client import fetch_cmc_quotes
    quotes = await fetch_cmc_quotes()
    bnb_q = quotes.get("BNB")
    if bnb_q and bnb_q.price > 0:
        bnb_price = bnb_q.price
        bnb_usd_val = bnb_bal * bnb_price
    from core import perp_math
    # Real positions only — shadow (paper-test) positions hold no capital.
    real_positions = [p for p in positions if not getattr(p, "is_shadow", False)]
    open_positions_usd = 0.0
    for p in real_positions:
        q = quotes.get(p.symbol.upper())
        # perp-aware: spot = units*price; perp = margin + directional uPnL
        open_positions_usd += perp_math.position_equity(p, q.price if (q and q.price > 0) else 0.0)

    total_portfolio_usd = usdt_bal + bnb_usd_val + open_positions_usd

    # Calculate PnL stats from closed trades
    closed_trades = [t for t in trades if t.status != "open"]
    total_pnl_usd = sum(t.pnl_usd for t in closed_trades)
    
    best_trade = 0.0
    worst_trade = 0.0
    wins = 0
    losses = 0
    
    for t in closed_trades:
        if t.pnl_usd > 0:
            wins += 1
        else:
            losses += 1
        
        if t.pnl_usd > best_trade:
            best_trade = t.pnl_usd
        if t.pnl_usd < worst_trade:
            worst_trade = t.pnl_usd

    total_closed = len(closed_trades)
    win_rate_pct = (wins / total_closed * 100.0) if total_closed > 0 else 0.0

    # Return percentage measured against the captured starting-equity baseline
    starting_capital = state.start_equity if state.start_equity > 0 else 100.0
    total_return_pct = (total_portfolio_usd - starting_capital) / starting_capital * 100.0

    # Fear and Greed
    fng_val = 50
    fng_lbl = "Neutral"
    fng_ann = "Fear & Greed index is currently neutral."
    try:
        fng_data = await get_fear_greed()
        if fng_data:
            fng_val = fng_data.get("value", 50)
            fng_lbl = fng_data.get("label", "Neutral")
            fng_ann = fng_data.get("annotation", "")
    except Exception as e:
        print(f"[OVERVIEW WARNING] Failed to fetch Fear & Greed: {e}")

    # Session performance breakdown
    comp_trades = [t for t in closed_trades if t.window == "COMPETITION"]
    qual_trades = [t for t in closed_trades if t.window == "QUALIFIER"]
    
    comp_wins = sum(1 for t in comp_trades if t.pnl_usd > 0)
    qual_wins = sum(1 for t in qual_trades if t.pnl_usd > 0)
    
    comp_rate = (comp_wins / len(comp_trades) * 100.0) if comp_trades else 0.0
    qual_rate = (qual_wins / len(qual_trades) * 100.0) if qual_trades else 0.0

    # Equity curve
    equity_pts = get_equity_history(session)
    equity_curve_list = []
    for pt in equity_pts:
        equity_curve_list.append({
            "t": pt.t,
            "equityUsd": pt.equity_usd
        })
        
    # If no equity curve data, seed with current
    if not equity_curve_list:
        equity_curve_list.append({
            "t": datetime.now(timezone.utc).isoformat(),
            "equityUsd": total_portfolio_usd
        })

    return {
        "asOf": datetime.now(timezone.utc).isoformat(),
        "mode": state.mode,
        "portfolio": {
            "totalUsd": round(total_portfolio_usd, 2),
            "totalReturnPct": round(total_return_pct, 2),
            "usdt": round(usdt_bal, 2),
            "bnb": round(bnb_bal, 4),
            "bnbUsd": round(bnb_usd_val, 2)
        },
        "pnl": {
            "totalUsd": round(total_pnl_usd, 2),
            "closedTrades": total_closed,
            "bestTradeUsd": round(best_trade, 2),
            "worstTradeUsd": round(worst_trade, 2)
        },
        "winRate": {
            "pct": round(win_rate_pct, 1),
            "wins": wins,
            "losses": losses
        },
        "openPositions": {
            "count": len(real_positions),
            "monitoredEverySec": 60
        },
        "fearGreed": {
            "value": fng_val,
            "label": fng_lbl,
            "annotation": fng_ann
        },
        "sessionPerf": {
            "competition": {
                "winRatePct": round(comp_rate, 1),
                "trades": f"{comp_wins}/{len(comp_trades)}"
            },
            "qualifier": {
                "winRatePct": round(qual_rate, 1),
                "trades": f"{qual_wins}/{len(qual_trades)}"
            }
        },
        "equityCurve": equity_curve_list
    }
