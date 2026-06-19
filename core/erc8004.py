"""ERC-8004 (BRC8004 on BNB Chain) on-chain agent registration.

The BNB Hack requires the agent to register its identity on the ERC-8004 Identity
Registry — an ERC-721 that mints an `agentId` NFT. `register(agentURI)` takes a URI
to an off-chain JSON "agent card". Registration is **nonpayable** (no fee) — only BSC
gas. Self-custody: the tx is signed locally by the SAME keystore wallet that trades
(core/agent_wallet.py), so there is no custodial step.

Registry (BSC mainnet, verified deployed): 0xfA09B3397fAC75424422C4D28b1729E3D4f659D7

Safety: register_agent() is a DRY RUN by default — it estimates gas and returns the
plan, broadcasting NOTHING. Pass send=True to actually sign + broadcast (the runbook
script requires an explicit --send flag), and only after the wallet is funded.
"""
import logging
from config import settings
from core.rpc import get_w3
from core.agent_wallet import get_agent_wallet

logger = logging.getLogger("xorr.core.erc8004")

# Minimal ABI: the register() write + balanceOf() read (to detect prior registration).
REGISTRY_ABI = [
    {"inputs": [{"name": "agentURI", "type": "string"}], "name": "register",
     "outputs": [{"name": "agentId", "type": "uint256"}], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
]


def _registry(w3):
    return w3.to_checksum_address(settings.erc8004_registry_address)


def is_registered() -> bool:
    """True if the agent wallet already owns an agent NFT in the Identity Registry."""
    try:
        w3 = get_w3()
        wallet = get_agent_wallet()
        if not wallet.address:
            return False
        c = w3.eth.contract(address=_registry(w3), abi=REGISTRY_ABI)
        return int(c.functions.balanceOf(w3.to_checksum_address(wallet.address)).call()) > 0
    except Exception as e:
        logger.warning(f"[ERC8004] is_registered check failed: {e}")
        return False


def register_agent(agent_uri: str = None, send: bool = False) -> dict:
    """Register the agent on the ERC-8004 Identity Registry.

    send=False (default) -> DRY RUN: estimates gas and returns the plan, broadcasts nothing.
    send=True            -> signs locally with the keystore wallet and broadcasts register(agentURI).
    """
    w3 = get_w3()
    wallet = get_agent_wallet()
    if not wallet.account or not wallet.address:
        return {"ok": False, "status": "no agent wallet configured (keystore missing)"}

    addr = w3.to_checksum_address(wallet.address)
    registry = _registry(w3)
    uri = (agent_uri or settings.agent_card_uri or "").strip()
    if not uri:
        return {"ok": False, "status": "agent_card_uri is empty — set AGENT_CARD_URI in .env"}

    c = w3.eth.contract(address=registry, abi=REGISTRY_ABI)
    fn = c.functions.register(uri)
    bal = int(w3.eth.get_balance(addr))
    gas_price = int(w3.eth.gas_price)
    try:
        gas = int(fn.estimate_gas({"from": addr}) * 1.25)
    except Exception:
        gas = 350_000  # registry estimate may fail pre-fund; use a safe default
    cost = gas * gas_price
    plan = {
        "ok": True, "registry": registry, "agentURI": uri, "from": addr,
        "gas": gas, "gasPriceGwei": round(gas_price / 1e9, 3),
        "estGasCostBNB": round(cost / 1e18, 6), "walletBNB": round(bal / 1e18, 6),
    }

    if is_registered():
        plan["status"] = "ALREADY REGISTERED — wallet already owns an agent NFT. Nothing to do."
        return plan

    if not send:
        plan["status"] = "DRY RUN — nothing broadcast. Fund the wallet, then re-run with --send."
        return plan

    if bal < cost:
        plan["ok"] = False
        plan["status"] = (f"INSUFFICIENT BNB for gas: need ~{cost / 1e18:.6f} BNB, "
                          f"have {bal / 1e18:.6f}. Send BNB to {addr} first.")
        return plan

    tx = fn.build_transaction({
        "from": addr, "nonce": w3.eth.get_transaction_count(addr),
        "gas": gas, "gasPrice": gas_price, "chainId": settings.bsc_chain_id,
    })
    signed = wallet.account.sign_transaction(tx)
    txh = w3.eth.send_raw_transaction(signed.raw_transaction)
    plan["txHash"] = w3.to_hex(txh)
    plan["status"] = "BROADCAST — waiting for confirmation..."
    try:
        rcpt = w3.eth.wait_for_transaction_receipt(txh, timeout=180)
        plan["blockNumber"] = rcpt.blockNumber
        plan["status"] = "REGISTERED on ERC-8004 ✅" if rcpt.status == 1 else "tx REVERTED on-chain"
    except Exception as e:
        plan["status"] = f"broadcast ok, receipt wait failed ({e}); check the tx hash on BscScan"
    return plan


# --- Competition contract registration (permissionless register(), no TWAK needed) ---
COMPETITION_ABI = [
    {"inputs": [], "name": "register", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "a", "type": "address"}], "name": "isRegistered",
     "outputs": [{"name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "registrationDeadline", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
]


def competition_is_registered() -> bool:
    try:
        w3 = get_w3()
        wallet = get_agent_wallet()
        if not wallet.address:
            return False
        c = w3.eth.contract(address=w3.to_checksum_address(settings.competition_contract), abi=COMPETITION_ABI)
        return bool(c.functions.isRegistered(w3.to_checksum_address(wallet.address)).call())
    except Exception as e:
        logger.warning(f"[COMPETE] is_registered check failed: {e}")
        return False


def register_competition(send: bool = False) -> dict:
    """Register the trading wallet on the competition contract via web3 (the same wallet
    that trades + is ERC-8004-registered). Permissionless register() — no TWAK creds
    needed. DRY RUN by default; send=True to broadcast."""
    w3 = get_w3()
    wallet = get_agent_wallet()
    if not wallet.account or not wallet.address:
        return {"ok": False, "status": "no agent wallet configured"}
    addr = w3.to_checksum_address(wallet.address)
    contract_addr = w3.to_checksum_address(settings.competition_contract)
    c = w3.eth.contract(address=contract_addr, abi=COMPETITION_ABI)

    plan = {"ok": True, "contract": contract_addr, "from": addr}
    try:
        plan["deadline"] = int(c.functions.registrationDeadline().call())
    except Exception:
        plan["deadline"] = None
    if competition_is_registered():
        plan["status"] = "ALREADY REGISTERED for the competition. Nothing to do."
        return plan

    fn = c.functions.register()
    bal = int(w3.eth.get_balance(addr))
    gas_price = int(w3.eth.gas_price)
    try:
        gas = int(fn.estimate_gas({"from": addr}) * 1.25)
    except Exception as e:
        plan["ok"] = False
        plan["status"] = f"register() would revert (deadline passed / not open?): {str(e)[:160]}"
        return plan
    cost = gas * gas_price
    plan.update({"gas": gas, "gasPriceGwei": round(gas_price / 1e9, 3),
                 "estGasCostBNB": round(cost / 1e18, 6), "walletBNB": round(bal / 1e18, 6)})
    if not send:
        plan["status"] = "DRY RUN — register() simulates OK, nothing broadcast. Re-run with send=True."
        return plan
    if bal < cost:
        plan["ok"] = False
        plan["status"] = f"INSUFFICIENT BNB for gas (need ~{cost / 1e18:.6f}, have {bal / 1e18:.6f})."
        return plan
    tx = fn.build_transaction({"from": addr, "nonce": w3.eth.get_transaction_count(addr),
                               "gas": gas, "gasPrice": gas_price, "chainId": settings.bsc_chain_id})
    signed = wallet.account.sign_transaction(tx)
    txh = w3.eth.send_raw_transaction(signed.raw_transaction)
    plan["txHash"] = w3.to_hex(txh)
    plan["status"] = "BROADCAST — waiting for confirmation..."
    try:
        rcpt = w3.eth.wait_for_transaction_receipt(txh, timeout=180)
        plan["blockNumber"] = rcpt.blockNumber
        plan["status"] = "REGISTERED for competition ✅" if rcpt.status == 1 else "tx REVERTED on-chain"
    except Exception as e:
        plan["status"] = f"broadcast ok, receipt wait failed ({e}); check the tx on BscScan"
    return plan
