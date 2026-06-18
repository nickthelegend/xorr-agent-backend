"""
Robust entrypoint for the XORR backend.

Run with:  python run.py
(equivalent to `uvicorn main:app` but self-contained, stays running, and prints
the agent wallet address + funding reminder on boot).

Keep this process running for the whole competition — it scans the eligible
universe continuously, opens risk-managed positions, and monitors/closes them.
"""
import os
import sys

# Ensure UTF-8 output on Windows consoles (non-ASCII token symbols)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("XORR_HOST", "127.0.0.1")
    port = int(os.environ.get("XORR_PORT", "8000"))
    print(f"[XORR] Starting backend on http://{host}:{port}  (Ctrl+C to stop)")
    print("[XORR] The engine scans + trades continuously while this process runs.")
    # reload=False so the process stays a single long-lived server
    uvicorn.run("main:app", host=host, port=port, reload=False, log_level="info")
