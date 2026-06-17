import io
import csv
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select
from typing import List, Optional
from api.deps import get_session
from persistence.models import Trade
from persistence.repo import get_trades

router = APIRouter()

def serialize_trade(t: Trade) -> dict:
    return {
        "id": t.id,
        "openedAt": t.opened_at,
        "closedAt": t.closed_at,
        "symbol": t.symbol,
        "contract": t.contract,
        "status": t.status,
        "invested": t.invested,
        "pnlUsd": t.pnl_usd,
        "pnlPct": t.pnl_pct,
        "holdMinutes": t.hold_minutes,
        "entryMarketCap": t.entry_mc,
        "exitMarketCap": t.exit_mc,
        "score": t.score,
        "exitReason": t.exit_reason,
        "bundlerPct": 0,  # 0 for spot
        "devPct": 0,
        "snipers": 0,
        "window": t.window,
        "txOpen": t.tx_open,
        "txClose": t.tx_close,
        "strategy": t.strategy
    }

@router.get("/trades")
def read_trades(
    window: str = Query("all", regex="^(all|competition|qualifier)$"),
    session: Session = Depends(get_session)
):
    trades = get_trades(session, window)
    return [serialize_trade(t) for t in trades]

@router.get("/trades/open")
def read_open_trades(session: Session = Depends(get_session)):
    statement = select(Trade).where(Trade.status == "open")
    trades = session.exec(statement).all()
    # Sort by opened_at descending
    trades_list = list(trades)
    trades_list.sort(key=lambda x: x.opened_at, reverse=True)
    return [serialize_trade(t) for t in trades_list]

@router.get("/trades/export.csv")
def export_trades_csv(session: Session = Depends(get_session)):
    trades = get_trades(session, "all")
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow([
        "id", "openedAt", "closedAt", "symbol", "contract", "status", 
        "invested", "pnlUsd", "pnlPct", "holdMinutes", "entryMarketCap", 
        "exitMarketCap", "score", "exitReason", "window", "txOpen", 
        "txClose", "strategy"
    ])
    
    # Write data
    for t in trades:
        writer.writerow([
            t.id, t.opened_at, t.closed_at, t.symbol, t.contract, t.status,
            t.invested, t.pnl_usd, t.pnl_pct, t.hold_minutes, t.entry_mc,
            t.exit_mc, t.score, t.exit_reason, t.window, t.tx_open,
            t.tx_close, t.strategy
        ])
    
    output.seek(0)
    
    headers = {
        'Content-Disposition': 'attachment; filename="xorr_trades_export.csv"'
    }
    
    return StreamingResponse(output, media_type="text/csv", headers=headers)
