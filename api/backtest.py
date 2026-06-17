import asyncio
import uuid
import json
import logging
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backtest.runner import run_walk_forward_backtest
from backtest.store import save_backtest_run, load_backtest_run, list_backtest_runs
from strategies.registry import STRATEGIES

logger = logging.getLogger("xorr.api.backtest")
router = APIRouter()

# Memory tracking for active running backtests progress
# run_id -> dict
RUNNING_BACKTESTS: Dict[str, Dict[str, Any]] = {}

class BacktestRequest(BaseModel):
    windowDays: int
    strategies: List[str]  # e.g. ["all"] or specific list
    qualityMode: bool

async def run_backtest_in_bg(
    run_id: str,
    window_days: int,
    strategies_list: List[str],
    quality_mode: bool
):
    try:
        RUNNING_BACKTESTS[run_id] = {
            "pct": 0,
            "trades_so_far": 0,
            "current_symbol": "",
            "status": "running",
            "report": None
        }

        # Resolve "all" strategies
        if len(strategies_list) == 1 and strategies_list[0].lower() == "all":
            actual_strategies = list(STRATEGIES.keys())
        else:
            actual_strategies = [s for s in strategies_list if s in STRATEGIES]

        if not actual_strategies:
            actual_strategies = list(STRATEGIES.keys())

        # Define progress updater callback
        def progress_callback(pct: int, trades_count: int, current_symbol: str):
            RUNNING_BACKTESTS[run_id]["pct"] = pct
            RUNNING_BACKTESTS[run_id]["trades_so_far"] = trades_count
            RUNNING_BACKTESTS[run_id]["current_symbol"] = current_symbol

        report = await run_walk_forward_backtest(
            window_days=window_days,
            strategies=actual_strategies,
            quality_mode=quality_mode,
            progress_callback=progress_callback
        )

        save_backtest_run(report)
        RUNNING_BACKTESTS[run_id]["status"] = "complete"
        RUNNING_BACKTESTS[run_id]["report"] = report
        logger.info(f"Backtest run {run_id} completed successfully and saved to DB.")
    except Exception as e:
        logger.error(f"Backtest run {run_id} failed in background: {e}", exc_info=True)
        RUNNING_BACKTESTS[run_id]["status"] = "failed"
        RUNNING_BACKTESTS[run_id]["error"] = str(e)

@router.post("/backtest/run", status_code=status.HTTP_202_ACCEPTED)
async def run_backtest(req: BacktestRequest, bg_tasks: BackgroundTasks):
    run_id = str(uuid.uuid4())
    bg_tasks.add_task(
        run_backtest_in_bg,
        run_id,
        req.windowDays,
        req.strategies,
        req.qualityMode
    )
    return {"runId": run_id}

@router.get("/backtest/runs")
async def list_runs():
    return list_backtest_runs()

@router.get("/backtest/runs/{run_id}")
async def get_run_report(run_id: str):
    # Try getting from DB first
    report = load_backtest_run(run_id)
    if report:
        from dataclasses import asdict
        return asdict(report)
        
    # Check if currently running in memory
    if run_id in RUNNING_BACKTESTS:
        info = RUNNING_BACKTESTS[run_id]
        if info["status"] == "complete" and info["report"]:
            from dataclasses import asdict
            return asdict(info["report"])
        elif info["status"] == "failed":
            raise HTTPException(status_code=500, detail=f"Backtest failed: {info.get('error')}")
        else:
            raise HTTPException(status_code=202, detail="Backtest is still running")
            
    raise HTTPException(status_code=404, detail="Backtest run not found")

@router.get("/stream/backtest/{run_id}")
async def stream_backtest_progress(run_id: str, request: Request):
    """SSE endpoint streaming backtest execution progress."""
    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break

                if run_id not in RUNNING_BACKTESTS:
                    # Not found yet, yield empty waiting message or break
                    yield f"event: error\ndata: {json.dumps({'error': 'Not found'})}\n\n"
                    break

                info = RUNNING_BACKTESTS[run_id]
                status = info["status"]
                
                payload = {
                    "pct": info["pct"],
                    "current_symbol": info["current_symbol"],
                    "trades_so_far": info["trades_so_far"],
                    "status": status
                }
                
                yield f"event: backtest_progress\ndata: {json.dumps(payload)}\n\n"

                if status in ["complete", "failed"]:
                    break

                await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Error in backtest SSE stream for {run_id}: {e}")
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")
