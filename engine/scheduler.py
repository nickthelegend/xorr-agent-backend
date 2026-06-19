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
        self._watchdog_task = None
        self._playbook_task = None
        self._executor = None
        self._scan_trigger_event = asyncio.Event()
        self._scan_heartbeat = 0.0
        self._monitor_heartbeat = 0.0

    def start(self):
        """Starts the background engine loops if they are not running."""
        if self._running:
            return
        self._running = True
        self._scan_trigger_event.clear()

        # Start the real-time WS price feed + liquidation feed alongside the loops
        try:
            from data import ws_feed, liq_feed
            ws_feed.ensure_started()
            liq_feed.ensure_started()
        except Exception as e:
            print(f"[SCHEDULER] feed start skipped: {e}")

        # Start loops
        self._scan_task = asyncio.create_task(self._scan_loop())
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())

        # Claude decision brain: periodic watchlist -> playbook refresh (subscription CLI)
        if bool(getattr(settings, "enable_claude_brain", False)):
            if self._playbook_task is None or self._playbook_task.done():
                self._playbook_task = asyncio.create_task(self._playbook_loop())

        print("[SCHEDULER] Engine scheduler started background tasks + watchdog.")

    def stop(self):
        """Pauses the scheduler scanning loops; open positions are still monitored."""
        self._running = False
        self._scan_trigger_event.set()  # release any waiting scans

        # Cancel scanning task
        if self._scan_task:
            self._scan_task.cancel()
            self._scan_task = None

        print("[SCHEDULER] Engine scheduler paused scanning. Monitoring is kept active.")

    def health(self) -> dict:
        """Liveness snapshot for a /health endpoint or external supervisor."""
        now = time.monotonic()
        return {
            "running": self._running,
            "scan_alive": bool(self._scan_task and not self._scan_task.done()),
            "monitor_alive": bool(self._monitor_task and not self._monitor_task.done()),
            "scan_age_sec": round(now - self._scan_heartbeat, 1) if self._scan_heartbeat else None,
            "monitor_age_sec": round(now - self._monitor_heartbeat, 1) if self._monitor_heartbeat else None,
        }

    async def _watchdog_loop(self):
        """Self-heal: if the scan or monitor loop task dies on an unhandled error,
        log it and respawn it. Keeps the agent alive unattended through the
        competition week (a dead loop = missed daily trades = 0 for those hours)."""
        while True:
            try:
                await asyncio.sleep(30.0)
            except asyncio.CancelledError:
                break
            # Restart the scan loop if it died while we should be running.
            if self._running and self._scan_task is not None and self._scan_task.done():
                exc = None
                if not self._scan_task.cancelled():
                    exc = self._scan_task.exception()
                if exc is not None or not self._scan_task.cancelled():
                    print(f"[WATCHDOG] scan loop ended unexpectedly (exc={exc}); restarting.")
                    self._scan_task = asyncio.create_task(self._scan_loop())
            # The monitor loop runs whenever not HALTED; restart if it died.
            if self._monitor_task is not None and self._monitor_task.done():
                exc = None
                if not self._monitor_task.cancelled():
                    exc = self._monitor_task.exception()
                print(f"[WATCHDOG] monitor loop ended unexpectedly (exc={exc}); restarting.")
                self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def _playbook_loop(self):
        """Rebuild the Claude playbook on a cadence (default every 4h). Refreshes once
        on boot, then sleeps the interval. Fail-soft — a bad cycle is logged, not fatal."""
        from claude.playbook import refresh_playbook
        while self._running:
            try:
                pb = await refresh_playbook()
                n = len(pb.get("picks", []))
                with Session(engine) as s:
                    await log_engine_msg(
                        s, "info",
                        f"[claude] playbook refreshed: {n} pick(s) (source={pb.get('source')}, regime={pb.get('regime')}).")
            except Exception as e:
                print(f"[CLAUDE] playbook refresh failed: {e}")
            try:
                await asyncio.sleep(float(getattr(settings, "watchlist_interval_hours", 4.0)) * 3600.0)
            except asyncio.CancelledError:
                break

    def trigger_scan(self):
        """Triggers an immediate scan execution."""
        self._scan_trigger_event.set()

    async def _scan_loop(self):
        """Loop that runs pipeline scans at standard intervals."""
        while self._running:
            self._scan_heartbeat = time.monotonic()
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

            # Wait for next interval or manual trigger (scan faster in simulation)
            try:
                with Session(engine) as session:
                    is_sim = get_state(session).mode == "simulation"
                interval = settings.sim_scan_interval_sec if is_sim else settings.scan_interval_sec
                await asyncio.wait_for(
                    self._scan_trigger_event.wait(),
                    timeout=float(interval)
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
        """Independent loop that polls active positions at the fast risk cadence."""
        while True:
            self._monitor_heartbeat = time.monotonic()
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

            # Fast risk/exit poll cadence (configurable; default 15s)
            try:
                await asyncio.sleep(float(settings.monitor_interval_sec))
            except asyncio.CancelledError:
                break

# Singleton scheduler instance
scheduler = EngineScheduler()
