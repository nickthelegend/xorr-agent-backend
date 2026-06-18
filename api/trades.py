import io
import csv
import time
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select
from typing import List, Optional, Dict
from api.deps import get_session
from persistence.models import Trade, Position
from persistence.repo import get_trades, get_positions
from data.cmc_client import fetch_cmc_quotes
from core import perp_math

router = APIRouter()


def _opened_ts(opened_at: str) -> float:
    try:
        return datetime.fromisoformat(opened_at).timestamp()
    except Exception:
        return 0.0


def serialize_trade(t: Trade, quotes: Dict = None, positions_by_id: Dict[str, Position] = None) -> dict:
    """Serializes a trade. For OPEN trades we compute the LIVE unrealized PnL,
    PnL%, hold time and current mark price from the matching open position and the
    latest quote — so the UI shows real numbers, not dashes."""
    quotes = quotes or {}
    positions_by_id = positions_by_id or {}

    entry_price = t.entry_price
    exit_price = t.exit_price
    mark_price = exit_price  # closed trades mark at their exit
    pnl_usd = t.pnl_usd
    pnl_pct = t.pnl_pct
    hold_minutes = t.hold_minutes

    pos = positions_by_id.get(t.id)
    is_perp = bool(getattr(pos, "is_perp", False)) if pos else (getattr(t, "venue", "spot") == "perp")
    direction = getattr(pos, "direction", None) or getattr(t, "direction", "long")
    leverage = getattr(pos, "leverage", None) or getattr(t, "leverage", 1.0)
    liquidation_price = getattr(pos, "liquidation_price", 0.0) if pos else 0.0

    if t.status == "open":
        quote = quotes.get(t.symbol.upper())
        if entry_price is None and pos is not None:
            entry_price = pos.entry_price
        if quote is not None and quote.price > 0:
            mark_price = quote.price
            if pos is not None and is_perp and t.invested > 0:
                # perp: PnL = directional uPnL on margin (NOT size*price = notional)
                upnl = perp_math.unrealized_pnl(direction, pos.size, pos.entry_price, quote.price)
                pnl_usd = round(upnl, 4)
                pnl_pct = round((upnl / t.invested) * 100.0, 2)  # return on margin
            elif pos is not None and pos.size > 0 and t.invested > 0:
                # spot long: cost-basis vs current value
                current_value = pos.size * quote.price
                pnl_usd = round(current_value - t.invested, 4)
                pnl_pct = round((pnl_usd / t.invested) * 100.0, 2)
            elif entry_price and entry_price > 0:
                pnl_pct = round(((quote.price - entry_price) / entry_price) * 100.0, 2)
                pnl_usd = round((pnl_pct / 100.0) * t.invested, 4)
        # live hold time
        opened = _opened_ts(t.opened_at)
        if opened > 0:
            hold_minutes = round((time.time() - opened) / 60.0, 1)

    return {
        "id": t.id,
        "openedAt": t.opened_at,
        "closedAt": t.closed_at,
        "symbol": t.symbol,
        "contract": t.contract,
        "status": t.status,
        "invested": t.invested,
        "pnlUsd": pnl_usd,
        "pnlPct": pnl_pct,
        "holdMinutes": hold_minutes,
        "entryPrice": entry_price,
        "exitPrice": exit_price,
        "markPrice": mark_price,
        "entryMarketCap": t.entry_mc,
        "exitMarketCap": t.exit_mc,
        "score": t.score,
        "exitReason": t.exit_reason,
        "unrealized": t.status == "open",
        "window": t.window,
        "txOpen": t.tx_open,
        "txClose": t.tx_close,
        "strategy": t.strategy,
        "isPerp": is_perp,
        "venue": "perp" if is_perp else "spot",
        "direction": direction,
        "leverage": leverage,
        "liquidationPrice": liquidation_price,
    }


async def _live_context(session: Session):
    """Fetch quotes + open positions once for live enrichment (fail-open)."""
    quotes = {}
    try:
        quotes = await fetch_cmc_quotes()
    except Exception:
        quotes = {}
    positions_by_id = {p.id: p for p in get_positions(session)}
    return quotes, positions_by_id


@router.get("/trades")
async def read_trades(
    window: str = Query("all", regex="^(all|competition|qualifier)$"),
    session: Session = Depends(get_session)
):
    trades = get_trades(session, window)
    quotes, positions_by_id = await _live_context(session)
    return [serialize_trade(t, quotes, positions_by_id) for t in trades]


@router.get("/trades/open")
async def read_open_trades(session: Session = Depends(get_session)):
    statement = select(Trade).where(Trade.status == "open")
    trades_list = list(session.exec(statement).all())
    trades_list.sort(key=lambda x: x.opened_at, reverse=True)
    quotes, positions_by_id = await _live_context(session)
    return [serialize_trade(t, quotes, positions_by_id) for t in trades_list]


@router.get("/trades/export.csv")
async def export_trades_csv(session: Session = Depends(get_session)):
    trades = get_trades(session, "all")
    quotes, positions_by_id = await _live_context(session)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "openedAt", "closedAt", "symbol", "contract", "status",
        "invested", "pnlUsd", "pnlPct", "holdMinutes", "entryPrice", "exitPrice",
        "markPrice", "entryMarketCap", "exitMarketCap", "score", "exitReason",
        "window", "txOpen", "txClose", "strategy"
    ])
    for t in trades:
        s = serialize_trade(t, quotes, positions_by_id)
        writer.writerow([
            s["id"], s["openedAt"], s["closedAt"], s["symbol"], s["contract"], s["status"],
            s["invested"], s["pnlUsd"], s["pnlPct"], s["holdMinutes"], s["entryPrice"], s["exitPrice"],
            s["markPrice"], s["entryMarketCap"], s["exitMarketCap"], s["score"], s["exitReason"],
            s["window"], s["txOpen"], s["txClose"], s["strategy"]
        ])

    output.seek(0)
    headers = {'Content-Disposition': 'attachment; filename="xorr_trades_export.csv"'}
    return StreamingResponse(output, media_type="text/csv", headers=headers)
