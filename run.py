"""
Robust entrypoint for the XORR backend.

Run with:
  python run.py            # venue from .env (SPOT_ONLY / ENABLE_PERPS)
  python run.py --spot     # force SPOT-ONLY this run (long-only spot; perps off)
  python run.py --perps    # force SPOT + PERPS this run (long/short, leverage)

(equivalent to `uvicorn main:app` but self-contained, stays running, and prints
the agent wallet address + funding reminder on boot).

Keep this process running for the whole competition — it scans the eligible
universe continuously, opens risk-managed positions, and monitors/closes them.

The --spot/--perps flags only set SPOT_ONLY/ENABLE_PERPS env vars before the app
loads — no perp code is removed, so you can switch venue per run.
"""
import argparse
import os
import sys

# Ensure UTF-8 output on Windows consoles (non-ASCII token symbols)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="XORR backend")
    parser.add_argument("--spot", action="store_true",
                        help="force SPOT-ONLY mode (long-only spot, no perps) for this run")
    parser.add_argument("--perps", action="store_true",
                        help="force SPOT + PERPS mode (long/short, leverage) for this run")
    args, _ = parser.parse_known_args()
    if args.spot and args.perps:
        print("[XORR] --spot and --perps are mutually exclusive; pick one.")
        sys.exit(2)
    # Set venue env BEFORE the app imports config (pydantic reads env > .env).
    if args.spot:
        os.environ["SPOT_ONLY"] = "true"
        os.environ["ENABLE_PERPS"] = "false"
        print("[XORR] Venue: SPOT-ONLY (long-only spot; perps disabled this run).")
    elif args.perps:
        os.environ["SPOT_ONLY"] = "false"
        os.environ["ENABLE_PERPS"] = "true"
        print("[XORR] Venue: SPOT + PERPS (long/short, leverage enabled this run).")
    else:
        _so = (os.environ.get("SPOT_ONLY") or "").strip().lower() in ("1", "true", "yes")
        print(f"[XORR] Venue: from .env -> {'SPOT-ONLY' if _so else 'SPOT + PERPS (or .env default)'}.")

    import uvicorn

    host = os.environ.get("XORR_HOST", "127.0.0.1")
    port = int(os.environ.get("XORR_PORT", "8000"))
    print(f"[XORR] Starting backend on http://{host}:{port}  (Ctrl+C to stop)")
    print("[XORR] The engine scans + trades continuously while this process runs.")
    # reload=False so the process stays a single long-lived server
    uvicorn.run("main:app", host=host, port=port, reload=False, log_level="info")
