"""Register the XORR agent on-chain (ERC-8004 Identity Registry on BNB Chain).

DRY RUN by default — shows the gas plan and broadcasts NOTHING. Add --send to actually
sign + broadcast the register(agentURI) tx (needs the wallet funded with a little BNB).

  python -m scripts.register_agent           # dry run: wallet address, gas plan, nothing sent
  python -m scripts.register_agent --send     # register for real (after funding BNB)
  python -m scripts.register_agent --uri https://.../agent_card.json --send

The agent registers with the SAME self-custody wallet it trades from. No fee — only gas.
"""
import argparse
import json
import os
import sys

# allow `python scripts/register_agent.py` from the repo root too
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.erc8004 import register_agent, is_registered  # noqa: E402
from core.agent_wallet import get_agent_wallet  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Register XORR on the ERC-8004 Identity Registry")
    ap.add_argument("--send", action="store_true", help="broadcast the tx (default: dry run)")
    ap.add_argument("--uri", default=None, help="override the agent-card URI")
    args = ap.parse_args()

    w = get_agent_wallet()
    print(f"Agent wallet : {w.address}")
    print(f"BNB balance  : {w.bnb_balance():.6f} BNB")
    print(f"Registered?  : {is_registered()}")
    print("-" * 60)

    res = register_agent(agent_uri=args.uri, send=args.send)
    print(json.dumps(res, indent=2))

    if not args.send and res.get("ok") and "ALREADY" not in res.get("status", ""):
        print("\nThis was a DRY RUN. Fund the wallet with a little BNB, then run with --send.")


if __name__ == "__main__":
    main()
