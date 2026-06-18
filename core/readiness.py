"""Go-live readiness check — the single source of truth for "am I ready to trade?".

Inspects the real environment (BSC connection, the funded wallet, TWAK creds,
registration, data keys) and returns a structured checklist + a capability summary
(can we trade spot live? perps live?). Fail-safe: any probe error becomes a failed
check, never an exception, so the dashboard can always render it.
"""
import os
import shutil
from typing import Dict, Any, List

from config import settings


def _placeholder(v: str) -> bool:
    v = (v or "").strip().lower()
    return (not v) or v.startswith("your_") or v in ("changeme", "xxx")


def check_readiness() -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def add(key, label, ok, detail="", optional=False, fix=""):
        checks.append({"key": key, "label": label, "ok": bool(ok),
                       "detail": detail, "optional": optional, "fix": fix})

    # --- chain connectivity ---
    connected = False
    block = 0
    try:
        from core.rpc import get_w3
        w3 = get_w3()
        connected = bool(w3.is_connected())
        block = int(w3.eth.block_number) if connected else 0
    except Exception as e:
        add("bsc", "BSC mainnet connection", False, f"error: {e}", fix="check BSC_RPC_URL")
    if connected:
        add("bsc", "BSC mainnet connection", True, f"block {block}")

    # --- self-custody wallet + funding ---
    addr = None
    bnb = usdt = 0.0
    try:
        from core.agent_wallet import get_agent_wallet
        w = get_agent_wallet()
        addr = w.address
        bnb = w.bnb_balance()
        usdt = w.token_balance(settings.usdt_contract)
    except Exception as e:
        add("wallet", "Self-custody wallet", False, f"error: {e}")
    if addr:
        add("wallet", "Self-custody wallet", True, addr)
        add("gas", "BNB for gas", bnb >= 0.003, f"{bnb:.4f} BNB",
            fix=f"send ~$8 of BNB to {addr}")
        add("funds", "USDT for trading", usdt >= 5.0, f"{usdt:.2f} USDT",
            fix=f"send ~$45-60 of USDT (BSC) to {addr}")

    # --- TWAK (optional: needed for PERPS + the special prize) ---
    twak_cli = shutil.which(settings.twak_bin) is not None
    twak_creds = bool((os.environ.get("TWAK_ACCESS_ID", settings.twak_access_id) or "").strip()
                      and (os.environ.get("TWAK_HMAC_SECRET", settings.twak_hmac_secret) or "").strip())
    add("twak_cli", "TWAK CLI installed", twak_cli, optional=True, fix="npm i -g @trustwallet/cli")
    add("twak_creds", "TWAK credentials (perps + prize)", twak_creds, optional=True,
        fix="run `twak setup`, paste TWAK_ACCESS_ID/HMAC into .env")

    # --- data providers ---
    add("cmc", "CoinMarketCap key", not _placeholder(settings.cmc_api_key) or not _placeholder(settings.cmc_mcp_api_key),
        detail="set", fix="set CMC_API_KEY in .env")
    add("groq", "Groq (LLM council) key", not _placeholder(settings.groq_api_key), detail="set",
        optional=True, fix="set GROQ_API_KEY (council fails open to deterministic if absent)")

    # --- competition registration ---
    registered = False
    mode = "simulation"
    try:
        from persistence.db import engine
        from sqlmodel import Session
        from persistence.repo import get_state
        with Session(engine) as s:
            st = get_state(s)
            registered = bool(st.registered)
            mode = st.mode
    except Exception:
        pass
    add("registered", "Registered on-chain (twak compete register)", registered,
        detail=("yes" if registered else "no"), fix="POST /api/engine/register (or `twak compete register`)")

    # --- capability summary ---
    spot_live = connected and bool(addr) and bnb >= 0.003 and usdt >= 5.0
    perps_live = spot_live and twak_cli and twak_creds

    required = [c for c in checks if not c["optional"]]
    required_ok = sum(1 for c in required if c["ok"])
    return {
        "mode": mode,
        "fundableAddress": addr,
        "capabilities": {
            "spotLive": spot_live,       # real on-chain spot via web3 — needs only a funded wallet
            "perpsLive": perps_live,     # needs TWAK CLI + creds too
            "simulation": True,          # always available
        },
        "requiredReady": f"{required_ok}/{len(required)}",
        "readyForSpotLive": spot_live,
        "readyForPerpsLive": perps_live,
        "checks": checks,
    }
