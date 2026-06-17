import time
import uuid
import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlmodel import Session
from api.deps import get_session, settings
from persistence.repo import get_state, update_state
from persistence.models import RuntimeState

router = APIRouter()

class ModeUpdate(BaseModel):
    mode: str

def write_audit_log(action: str, from_mode: str, to_mode: str):
    """Appends mode change to decisions.jsonl audit log."""
    log_entry = {
        "id": str(uuid.uuid4()),
        "t": datetime.now(timezone.utc).isoformat(),
        "symbol": "SYSTEM",
        "action": action,
        "strategy": "SYSTEM",
        "filters_passed": [],
        "filters_blocked": [],
        "brain_score": 0.0,
        "reasoning": f"Operator changed mode from {from_mode} to {to_mode}.",
        "market_snapshot": {}
    }
    try:
        os_dir = "data_store"
        import os
        os.makedirs(os_dir, exist_ok=True)
        with open(os.path.join(os_dir, "decisions.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception as e:
        print(f"[AUDIT ERROR] Failed to write mode change to audit log: {e}")

@router.post("/engine/mode")
async def change_mode(payload: ModeUpdate, session: Session = Depends(get_session)):
    target_mode = payload.mode
    if target_mode not in ["simulation", "live"]:
        raise HTTPException(status_code=400, detail="Invalid mode. Must be 'simulation' or 'live'")
    
    state = get_state(session)
    old_mode = state.mode
    
    if old_mode != target_mode:
        update_state(session, mode=target_mode)
        write_audit_log("MODE_CHANGE", old_mode, target_mode)
        
        # If live mode, verify TWAK executor is updated (dynamic reloading)
        from core.twak_executor import TwakExecutor
        # Update current active executors in cache if any
        from api.wallet import _executors
        if True in _executors:
            _executors[True].simulation = (target_mode == "simulation")
        if False in _executors:
            _executors[False].simulation = (target_mode == "simulation")

    return {
        "mode": target_mode,
        "success": True
    }

@router.get("/engine/registration")
async def get_registration(session: Session = Depends(get_session)):
    """Returns the competition registration status of the agent wallet."""
    state = get_state(session)
    from core.twak_executor import TwakExecutor
    executor = TwakExecutor(settings, simulation=(state.mode == "simulation"))
    try:
        address = await executor.get_address()
    except Exception:
        address = None
    return {
        "registered": state.registered,
        "tx": state.registered_tx,
        "address": address,
        "mode": state.mode,
        "contract": "0x212c61b9b72c95d95bf29cf032f5e5635629aed5",
    }


@router.post("/engine/register")
async def register_competition(session: Session = Depends(get_session)):
    """Registers the agent wallet on-chain for the competition (operator action).

    In simulation mode this is a no-op stub; in live mode it runs
    `twak compete register` via the executor and records the tx hash.
    """
    state = get_state(session)
    from core.twak_executor import TwakExecutor
    executor = TwakExecutor(settings, simulation=(state.mode == "simulation"))
    try:
        tx = await executor.register_for_competition()
        update_state(session, registered=True, registered_tx=tx)
        return {"success": True, "tx": tx, "simulated": executor.simulation}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Registration failed: {e}")


@router.post("/engine/scan")
async def trigger_scan(session: Session = Depends(get_session)):
    # Import scheduler and trigger immediate scan
    from engine.scheduler import scheduler
    if scheduler:
        asyncio_task = scheduler.trigger_scan()
        return {"success": True}
    return {"success": False, "error": "Scheduler not initialized"}

@router.post("/engine/start")
async def start_engine(force: bool = Query(False), session: Session = Depends(get_session)):
    from engine.scheduler import scheduler
    state = get_state(session)
    if state.scheduler_state == "HALTED" and not force:
        raise HTTPException(status_code=400, detail="Engine is HALTED due to risk limit. Use force=true to override.")
    
    if scheduler:
        scheduler.start()
        # Update state to IDLE or SCANNING/MONITORING
        current_state = "IDLE" if state.scheduler_state == "HALTED" else state.scheduler_state
        update_state(session, scheduler_state=current_state)
        return {"success": True, "state": current_state}
    return {"success": False, "error": "Scheduler not initialized"}

@router.post("/engine/stop")
async def stop_engine(session: Session = Depends(get_session)):
    from engine.scheduler import scheduler
    if scheduler:
        scheduler.stop()
        # Update state to IDLE (or custom PAUSED state if needed, let's keep IDLE)
        # Note: Open positions are still monitored in stop mode, but scheduler scans are paused.
        return {"success": True, "state": "IDLE"}
    return {"success": False, "error": "Scheduler not initialized"}
