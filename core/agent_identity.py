"""
On-chain agent identity via the BNB AI Agent SDK (`bnbagent`) — ERC-8004 Identity
Registry. This gives XORR a verifiable, discoverable on-chain identity bound to its
self-custody wallet (targets the "Best Use of BNB AI Agent SDK" prize).

Uses the SAME keystore wallet as trading (core/agent_wallet). On bsc-testnet the SDK
routes through a paymaster, so registration is gas-free — it can be done without
funding. Mainnet registration needs BNB for gas.
"""
import logging
from typing import Optional, Dict, Any

from config import settings
from core.agent_wallet import get_agent_wallet

logger = logging.getLogger("xorr.core.agent_identity")

DEFAULT_NETWORK = "bsc-testnet"   # gas-free via paymaster
AGENT_NAME = "XORR"


def _agent(network: str = DEFAULT_NETWORK):
    from bnbagent import ERC8004Agent, EVMWalletProvider
    w = get_agent_wallet()
    if not w.account:
        raise RuntimeError("Agent wallet unavailable")
    pk = "0x" + w.account.key.hex()
    provider = EVMWalletProvider(password=settings.twak_password, private_key=pk, persist=False)
    return ERC8004Agent(provider, network=network)


def identity_status(network: str = DEFAULT_NETWORK) -> Dict[str, Any]:
    """Read-only: the agent's wallet, the ERC-8004 registry, and local registration state."""
    try:
        agent = _agent(network)
        local = None
        try:
            local = agent.get_local_agent_info(AGENT_NAME)
        except Exception:
            local = None
        return {
            "walletAddress": agent.wallet_address,
            "registryContract": agent.contract_address,
            "network": network,
            "registered": bool(local),
            "local": local,
        }
    except Exception as e:
        return {"error": str(e), "network": network, "registered": False}


def register_identity(network: str = DEFAULT_NETWORK, endpoint_url: Optional[str] = None) -> Dict[str, Any]:
    """Registers XORR's ERC-8004 on-chain identity (operator action). Idempotent
    against local state. Gas-free on testnet via the SDK paymaster."""
    from bnbagent import AgentEndpoint
    agent = _agent(network)

    existing = None
    try:
        existing = agent.get_local_agent_info(AGENT_NAME)
    except Exception:
        existing = None
    if existing:
        return {"alreadyRegistered": True, "network": network, "local": existing}

    endpoints = [AgentEndpoint(
        name="api",
        endpoint=endpoint_url or "http://localhost:8000",
        version="2.0.0",
        capabilities=["spot-trading", "bnb-chain", "pancakeswap", "self-custody"],
    )]
    uri = agent.generate_agent_uri(
        name=AGENT_NAME,
        description="XORR — autonomous self-custody trading agent on BNB Chain (BNB Hack Track 1).",
        endpoints=endpoints,
    )
    result = agent.register_agent(uri, metadata=[
        {"key": "framework", "value": "XORR"},
        {"key": "track", "value": "BNB-Hack-Track-1"},
    ])
    return {"registered": True, "network": network, "uri": uri, "result": result}
