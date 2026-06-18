"""
Real self-custody agent wallet for BNB Smart Chain.

Follows the pattern used by bking / NEXUS: the agent owns a real BSC keypair and
signs locally — no custodial step. The address is real (and shown even in
simulation, so it can be funded and verified), balances are read live from chain,
and live swaps are executed on PancakeSwap V2 by locally signing the transaction.

Key source priority:
  1. settings.agent_private_key / env AGENT_PRIVATE_KEY  (explicit)
  2. encrypted keystore  data_store/agent_keystore.json  (decrypted with TWAK pw)
  3. otherwise a fresh key is generated, saved as an encrypted keystore, and the
     address is logged with a "FUND THIS ADDRESS" notice.

On the competition machine the Trust Wallet Agent Kit CLI manages its own wallet;
TwakExecutor prefers the `twak` CLI when it is installed and falls back to this
local keystore otherwise.
"""
import os
import json
import time
import logging
from pathlib import Path
from typing import Optional, List

from web3 import Web3
from eth_account import Account

from config import settings
from core.rpc import get_w3, get_decimals, MIN_ERC20_ABI

logger = logging.getLogger("xorr.core.agent_wallet")

KEYSTORE_PATH = Path("data_store/agent_keystore.json")

# PancakeSwap V2 Router
PANCAKE_ROUTER_ABI = [
    {"name": "getAmountsOut", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "amountIn", "type": "uint256"}, {"name": "path", "type": "address[]"}],
     "outputs": [{"name": "amounts", "type": "uint256[]"}]},
    {"name": "swapExactTokensForTokens", "type": "function", "stateMutability": "nonpayable",
     "inputs": [
         {"name": "amountIn", "type": "uint256"},
         {"name": "amountOutMin", "type": "uint256"},
         {"name": "path", "type": "address[]"},
         {"name": "to", "type": "address"},
         {"name": "deadline", "type": "uint256"}],
     "outputs": [{"name": "amounts", "type": "uint256[]"}]},
    {"name": "swapExactTokensForTokensSupportingFeeOnTransferTokens", "type": "function", "stateMutability": "nonpayable",
     "inputs": [
         {"name": "amountIn", "type": "uint256"},
         {"name": "amountOutMin", "type": "uint256"},
         {"name": "path", "type": "address[]"},
         {"name": "to", "type": "address"},
         {"name": "deadline", "type": "uint256"}],
     "outputs": []},
]

ERC20_ABI = MIN_ERC20_ABI + [
    {"constant": False, "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
]

MAX_UINT = 2 ** 256 - 1


class SwapResult:
    def __init__(self, success, tx_hash="", amount_in=0.0, amount_out=0.0, executed_price=0.0, error=None):
        self.success = success
        self.tx_hash = tx_hash
        self.amount_in = amount_in
        self.amount_out = amount_out
        self.executed_price = executed_price
        self.error = error


class AgentWallet:
    def __init__(self):
        self.w3: Web3 = get_w3()
        self.account = self._load_or_create()
        self.address: Optional[str] = self.account.address if self.account else None
        self.usdt = self.w3.to_checksum_address(settings.usdt_contract)
        self.wbnb = self.w3.to_checksum_address(settings.wbnb_contract)
        self.router = self.w3.to_checksum_address(settings.pancake_router)

    # ---- key management -------------------------------------------------
    def _password(self) -> str:
        return os.environ.get(settings.twak_password_env, settings.twak_password)

    def _load_or_create(self):
        pk = (os.environ.get("AGENT_PRIVATE_KEY") or settings.agent_private_key or "").strip()
        if pk:
            try:
                return Account.from_key(pk)
            except Exception as e:
                logger.error(f"[WALLET] invalid AGENT_PRIVATE_KEY: {e}")

        if KEYSTORE_PATH.exists():
            try:
                with open(KEYSTORE_PATH, "r", encoding="utf-8") as f:
                    ks = json.load(f)
                key = Account.decrypt(ks, self._password())
                return Account.from_key(key)
            except Exception as e:
                logger.error(f"[WALLET] failed to decrypt keystore: {e}")

        # Generate a fresh self-custody wallet
        try:
            acct = Account.create()
            ks = Account.encrypt(acct.key, self._password())
            KEYSTORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(KEYSTORE_PATH, "w", encoding="utf-8") as f:
                json.dump(ks, f)
            logger.warning(
                f"[WALLET] Generated NEW self-custody agent wallet {acct.address}. "
                f"FUND THIS ADDRESS with BNB (gas) + USDT (trading). "
                f"Encrypted keystore saved to {KEYSTORE_PATH}."
            )
            return acct
        except Exception as e:
            logger.error(f"[WALLET] could not create keystore wallet: {e}")
            return None

    # ---- balances (live, on-chain) -------------------------------------
    def bnb_balance(self) -> float:
        if not self.address:
            return 0.0
        try:
            return float(self.w3.eth.get_balance(self.w3.to_checksum_address(self.address))) / 1e18
        except Exception as e:
            logger.warning(f"[WALLET] bnb_balance failed: {e}")
            return 0.0

    def token_balance(self, token_address: str) -> float:
        if not self.address:
            return 0.0
        try:
            c = self.w3.eth.contract(address=self.w3.to_checksum_address(token_address), abi=ERC20_ABI)
            raw = c.functions.balanceOf(self.w3.to_checksum_address(self.address)).call()
            dec = get_decimals(token_address)
            return float(raw) / (10 ** dec)
        except Exception as e:
            logger.warning(f"[WALLET] token_balance failed for {token_address}: {e}")
            return 0.0

    # ---- quoting --------------------------------------------------------
    def _path(self, token_in: str, token_out: str) -> List[str]:
        ti = self.w3.to_checksum_address(token_in)
        to = self.w3.to_checksum_address(token_out)
        if ti == self.wbnb or to == self.wbnb:
            return [ti, to]
        # route through WBNB (deepest liquidity on PancakeSwap)
        return [ti, self.wbnb, to]

    def quote_out(self, token_in: str, token_out: str, amount_in_wei: int) -> int:
        """Expected output (wei) for amount_in via getAmountsOut. 0 on failure."""
        try:
            router = self.w3.eth.contract(address=self.router, abi=PANCAKE_ROUTER_ABI)
            amounts = router.functions.getAmountsOut(int(amount_in_wei), self._path(token_in, token_out)).call()
            return int(amounts[-1])
        except Exception as e:
            logger.warning(f"[WALLET] quote_out failed {token_in}->{token_out}: {e}")
            return 0

    # ---- approvals + swap (live, signs locally) ------------------------
    def _ensure_allowance(self, token: str, amount_wei: int) -> Optional[str]:
        c = self.w3.eth.contract(address=self.w3.to_checksum_address(token), abi=ERC20_ABI)
        owner = self.w3.to_checksum_address(self.address)
        current = c.functions.allowance(owner, self.router).call()
        if current >= amount_wei:
            return None  # already approved
        tx = c.functions.approve(self.router, MAX_UINT).build_transaction({
            "from": owner,
            "nonce": self.w3.eth.get_transaction_count(owner),
            "gas": settings.approve_gas_limit,
            "gasPrice": self.w3.eth.gas_price,
            "chainId": settings.bsc_chain_id,
        })
        signed = self.account.sign_transaction(tx)
        txh = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        self.w3.eth.wait_for_transaction_receipt(txh, timeout=120)
        return self.w3.to_hex(txh)

    def swap_tokens(self, token_in: str, token_out: str, amount_in_wei: int, min_out_wei: int) -> SwapResult:
        """Executes an exact-in PancakeSwap V2 swap, signing locally. Returns the
        realized output read from the resulting balance delta."""
        if not self.account or not self.address:
            return SwapResult(False, error="No agent wallet configured")
        try:
            owner = self.w3.to_checksum_address(self.address)
            token_out_cs = self.w3.to_checksum_address(token_out)

            # 1. Approve token_in to the router if needed
            self._ensure_allowance(token_in, amount_in_wei)

            # 2. Balance of token_out before (to measure realized output)
            out_c = self.w3.eth.contract(address=token_out_cs, abi=ERC20_ABI)
            bal_before = out_c.functions.balanceOf(owner).call()

            router = self.w3.eth.contract(address=self.router, abi=PANCAKE_ROUTER_ABI)
            deadline = int(time.time()) + settings.swap_deadline_sec
            path = self._path(token_in, token_out)

            tx = router.functions.swapExactTokensForTokens(
                int(amount_in_wei), int(min_out_wei), path, owner, deadline
            ).build_transaction({
                "from": owner,
                "nonce": self.w3.eth.get_transaction_count(owner),
                "gas": settings.swap_gas_limit,
                "gasPrice": self.w3.eth.gas_price,
                "chainId": settings.bsc_chain_id,
            })
            signed = self.account.sign_transaction(tx)
            txh = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(txh, timeout=settings.swap_deadline_sec + 60)

            tx_hex = self.w3.to_hex(txh)
            if receipt.status != 1:
                return SwapResult(False, tx_hash=tx_hex, error="Swap transaction reverted on-chain")

            bal_after = out_c.functions.balanceOf(owner).call()
            dec_out = get_decimals(token_out)
            amount_out = float(bal_after - bal_before) / (10 ** dec_out)
            dec_in = get_decimals(token_in)
            amount_in = float(amount_in_wei) / (10 ** dec_in)
            return SwapResult(
                success=True, tx_hash=tx_hex, amount_in=amount_in, amount_out=amount_out,
                executed_price=(amount_in / amount_out) if amount_out > 0 else 0.0,
            )
        except Exception as e:
            logger.error(f"[WALLET] swap_tokens failed: {e}")
            return SwapResult(False, error=str(e))


_agent_wallet: Optional[AgentWallet] = None


def get_agent_wallet() -> AgentWallet:
    """Process-wide singleton (lazily created on first use)."""
    global _agent_wallet
    if _agent_wallet is None:
        _agent_wallet = AgentWallet()
    return _agent_wallet
