"""Register the XORR agent on-chain — BOTH registrations, from the same self-custody
wallet that trades (no TWAK creds needed; competition contract is a permissionless
register()).

  1. ERC-8004 Identity Registry (BRC8004)  — mints the agent identity NFT
  2. Competition contract (0x212c…aed5)     — enrolls the wallet for scoring

DRY RUN by default — shows the plans and broadcasts NOTHING. Add --send to broadcast
(needs a little BNB for gas; each is ~$0.003–0.01). Re-runs are safe: each step
short-circuits if the wallet is already registered.

  python -m scripts.register_agent            # dry run, nothing sent
  python -m scripts.register_agent --send      # both registrations for real
  python -m scripts.register_agent --send --erc8004-only   # just ERC-8004
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.erc8004 import (  # noqa: E402
    register_agent, is_registered,
    register_competition, competition_is_registered,
)
from core.agent_wallet import get_agent_wallet  # noqa: E402


def _persist_registered():
    """Record registration in RuntimeState so the agent won't retry via TWAK on boot."""
    try:
        from persistence.db import engine
        from sqlmodel import Session
        from persistence.repo import get_state
        with Session(engine) as s:
            st = get_state(s)
            st.registered = True
            s.add(st)
            s.commit()
    except Exception as e:
        print(f"(note: could not persist registered flag: {e})")


def main():
    ap = argparse.ArgumentParser(description="Register XORR on-chain (ERC-8004 + competition)")
    ap.add_argument("--send", action="store_true", help="broadcast the txs (default: dry run)")
    ap.add_argument("--uri", default=None, help="override the ERC-8004 agent-card URI")
    ap.add_argument("--erc8004-only", action="store_true", help="skip the competition contract")
    ap.add_argument("--competition-only", action="store_true", help="skip ERC-8004")
    args = ap.parse_args()

    w = get_agent_wallet()
    print(f"Agent wallet : {w.address}")
    print(f"BNB balance  : {w.bnb_balance():.6f} BNB")
    print(f"ERC-8004 reg : {is_registered()}   |   competition reg : {competition_is_registered()}")
    print("=" * 64)

    ok = True
    if not args.competition_only:
        print("\n[1] ERC-8004 Identity Registry")
        r1 = register_agent(agent_uri=args.uri, send=args.send)
        print(json.dumps(r1, indent=2))
        ok = ok and r1.get("ok", False)

    if not args.erc8004_only:
        print("\n[2] Competition contract")
        r2 = register_competition(send=args.send)
        print(json.dumps(r2, indent=2))
        ok = ok and r2.get("ok", False)

    if args.send and ok:
        _persist_registered()
        print("\n✅ Done. Submit your wallet address on DoraHacks to complete entry.")
    elif not args.send:
        print("\nThis was a DRY RUN. Fund a little BNB, then re-run with --send.")


if __name__ == "__main__":
    main()
