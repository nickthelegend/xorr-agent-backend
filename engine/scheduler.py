import asyncio
import time
from datetime import datetime, timezone
from sqlmodel import Session
from config import settings
from persistence.db import engine
from persistence.repo import get_state, update_state
from core.twak_executor import TwakExecutor
from engine.pipeline import run_pipeline_cycle
from engine.monitor import monitor_tick
from api.stream import log_engine_msg

class EngineScheduler:
    def __init__(self):
        self._running = False
        self._scan_task = None
        self._monitor_task = None
        self._executor = None
        self._scan_trigger_event = asyncio.Event()

    def start(self):
        """Starts the background engine loops if they are not running."""
        if self._running:
            return
        self._running = True
        self._scan_trigger_event.clear()
        
        # Start loops
        self._scan_task = asyncio.create_task(self._scan_loop())
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        
        print("[SCHEDULER] Engine scheduler started background tasks.")

    def stop(self):
        """Pauses the scheduler scanning loops; open positions are still monitored."""
        self._running = False
        self._scan_trigger_event.set()  # release any waiting scans
        
        # Cancel scanning task
        if self._scan_task:
            self._scan_task.cancel()
            self._scan_task = None
            
        print("[SCHEDULER] Engine scheduler paused scanning. Monitoring is kept active.")

    def trigger_scan(self):
        """Triggers an immediate scan execution."""
        self._scan_trigger_event.set()

    async def _scan_loop(self):
        """Loop that runs pipeline scans at standard intervals."""
        while self._running:
            # Check state in database
            with Session(engine) as session:
                state = get_state(session)
                if state.scheduler_state == "HALTED":
                    await asyncio.sleep(5)
                    continue
                    
                # Update status
                update_state(session, scheduler_state="SCANNING")
                self._executor = TwakExecutor(settings, simulation=(state.mode == "simulation"))

            try:
                with Session(engine) as session:
                    await run_pipeline_cycle(session, self._executor)
            except Exception as e:
                print(f"[SCHEDULER ERROR] Scan loop cycle failed: {e}")

            # Reset status back to IDLE
            with Session(engine) as session:
                state = get_state(session)
                if state.scheduler_state != "HALTED":
                    update_state(session, scheduler_state="IDLE")

            # Wait for next interval or manual trigger
            try:
                # wait for trigger or timeout (default scan interval)
                await asyncio.wait_for(
                    self._scan_trigger_event.wait(),
                    timeout=float(settings.scan_interval_sec)
                )
                # If we were triggered, clear the event
                self._scan_trigger_event.clear()
                print("[SCHEDULER] Manual scan trigger received. Running scan now.")
            except asyncio.TimeoutError:
                # standard timeout, continue loop
                pass
            except asyncio.CancelledError:
                break

    async def _monitor_loop(self):
        """Independent loop that polls active positions every 60 seconds."""
        while True:
            # We monitor positions even if scanning is stopped, but not if HALTED
            with Session(engine) as session:
                state = get_state(session)
                if state.scheduler_state == "HALTED":
                    await asyncio.sleep(5)
                    continue
                
                # Check status
                if self._running:
                    # Update state to MONITORING briefly during evaluation
                    # only if we are currently IDLE
                    if state.scheduler_state == "IDLE":
                        update_state(session, scheduler_state="MONITORING")
                
                # Dynamic mode check
                executor = TwakExecutor(settings, simulation=(state.mode == "simulation"))

            try:
                with Session(engine) as session:
                    await monitor_tick(session, executor)
            except Exception as e:
                print(f"[SCHEDULER ERROR] Monitor tick failed: {e}")

            # Reset status back to IDLE
            with Session(engine) as session:
                state = get_state(session)
                if state.scheduler_state == "MONITORING":
                    update_state(session, scheduler_state="IDLE")

            # Poll cadence: 60s
            try:
                await asyncio.sleep(60.0)
            except asyncio.CancelledError:
                break

# Singleton scheduler instance
scheduler = EngineScheduler()
