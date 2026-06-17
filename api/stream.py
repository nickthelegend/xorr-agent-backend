import asyncio
import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session
from api.deps import get_session
from persistence.repo import get_engine_logs

router = APIRouter()

class SSEBroadcaster:
    def __init__(self):
        self._listeners = set()

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue()
        self._listeners.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._listeners.discard(q)

    async def broadcast(self, event_name: str, data: dict):
        msg = f"event: {event_name}\ndata: {json.dumps(data)}\n\n"
        # Gather all listener tasks
        coros = [q.put(msg) for q in list(self._listeners)]
        if coros:
            await asyncio.gather(*coros, return_exceptions=True)

# Global broadcasters
log_broadcaster = SSEBroadcaster()
equity_broadcaster = SSEBroadcaster()

# Helper to push a new engine log to DB and stream
async def log_engine_msg(session: Session, level: str, msg: str):
    from persistence.repo import add_engine_log
    log_entry = add_engine_log(session, level, msg)
    # Broadcast to SSE listeners
    await log_broadcaster.broadcast("log", {
        "t": log_entry.t,
        "level": log_entry.level,
        "msg": log_entry.msg
    })

# Throttle for persisting equity points (seconds between DB writes)
_last_equity_persist = 0.0
_EQUITY_PERSIST_INTERVAL = 60.0

# Helper to push a new equity tick: stream it, persist a throttled point for the
# equity curve, and raise the peak-equity high-water mark for the risk engine.
async def tick_equity_val(equity_usd: float):
    global _last_equity_persist
    now_ts = datetime.now(timezone.utc).timestamp()

    await equity_broadcaster.broadcast("tick", {
        "t": datetime.now(timezone.utc).isoformat(),
        "equityUsd": round(equity_usd, 2)
    })

    if now_ts - _last_equity_persist >= _EQUITY_PERSIST_INTERVAL:
        _last_equity_persist = now_ts
        try:
            from sqlmodel import Session
            from persistence.db import engine
            from persistence.repo import add_equity_point, get_state, update_state
            with Session(engine) as session:
                add_equity_point(session, round(equity_usd, 2))
                state = get_state(session)
                if equity_usd > state.peak_equity:
                    update_state(session, peak_equity=equity_usd)
        except Exception as e:
            print(f"[EQUITY PERSIST WARNING] {e}")

@router.get("/stream/log")
async def stream_logs(request: Request, session: Session = Depends(get_session)):
    q = log_broadcaster.subscribe()
    
    async def event_generator():
        try:
            # Seed connection with recent logs from DB
            recent_logs = get_engine_logs(session, limit=30)
            for entry in recent_logs:
                yield f"event: log\ndata: {json.dumps({'t': entry.t, 'level': entry.level, 'msg': entry.msg})}\n\n"
                
            # Loop for new live logs
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break
                
                try:
                    # Non-blocking wait for new broadcast log with 15s timeout for heartbeats
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield msg
                except asyncio.TimeoutError:
                    # Heartbeat to keep connection alive
                    yield f"event: heartbeat\ndata: {json.dumps({'ping': True})}\n\n"
        finally:
            log_broadcaster.unsubscribe(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.get("/stream/equity")
async def stream_equity(request: Request):
    q = equity_broadcaster.subscribe()
    
    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield msg
                except asyncio.TimeoutError:
                    yield f"event: heartbeat\ndata: {json.dumps({'ping': True})}\n\n"
        finally:
            equity_broadcaster.unsubscribe(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
