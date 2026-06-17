import os
import json
import re
import shutil
import asyncio
import random
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from config import settings
from core.types import Quote, ExecutionResult
from core.rpc import get_balance_of, get_decimals

# We use a real burner address for simulation mode
BURNER_ADDRESS = "0x7777777000000000000000000000000000000777"

class TwakError(Exception):
    pass

class TwakExecutor:
    def __init__(self, settings_obj=settings, simulation: bool = True):
        self.settings = settings_obj
        self.simulation = simulation
        self.password = os.environ.get(self.settings.twak_password_env, self.settings.twak_password)
        self.twak_bin = shutil.which(self.settings.twak_bin) or self.settings.twak_bin
        self._cached_address: Optional[str] = None
        self._consecutive_timeouts = 0

        # Simulated state (balances) when on-chain burner is empty
        self._sim_usdt_balance = 100.0
        self._sim_bnb_balance = 0.5
        self._sim_token_balances: Dict[str, float] = {}

    def _extract_json(self, text: str) -> dict:
        if not text:
            return {}
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass
        matches = re.findall(r"\{.*\}", text, re.S)
        for m in reversed(matches):
            try:
                return json.loads(m)
            except json.JSONDecodeError:
                continue
        return {}

    async def _run_twak(self, args: list, timeout: int = 30) -> dict:
        if self.simulation:
            # Twak calls are bypassed in simulation mode
            return {"error": "Bypassed in simulation mode"}

        env = os.environ.copy()
        if self.password:
            env["TWAK_WALLET_PASSWORD"] = self.password
        env["TWAK_NO_ANALYTICS"] = "1"
        
        cmd = [self.twak_bin] + args + ["--json", "--no-analytics"]
        
        try:
            # Run using asyncio subprocess for non-blocking execution
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                stdout = stdout_bytes.decode("utf-8", errors="ignore")
                stderr = stderr_bytes.decode("utf-8", errors="ignore")
                
                self._consecutive_timeouts = 0  # reset timeout counter
                
                data = self._extract_json(stdout) or self._extract_json(stderr)
                if proc.returncode != 0 and not data:
                    raise TwakError(f"TWAK process returned code {proc.returncode}. Stderr: {stderr[:300]}")
                return data or {}
            except asyncio.TimeoutError:
                self._consecutive_timeouts += 1
                if self._consecutive_timeouts >= 2:
                    print("[ENGINE] TWAK degraded: Timeout threshold hit.")
                    # In real pipeline, this will print and prompt a pause.
                try:
                    proc.kill()
                except Exception:
                    pass
                raise TwakError("TWAK execution timed out.")
        except Exception as e:
            if isinstance(e, TwakError):
                raise e
            raise TwakError(f"Subprocess start failed: {e}")

    async def get_address(self) -> str:
        if self.simulation:
            return BURNER_ADDRESS
        if self._cached_address:
            return self._cached_address
        
        try:
            data = await self._run_twak(["wallet", "address", "--chain", "bsc"])
            addr = data.get("address") or data.get("0") or ""
            if addr:
                self._cached_address = addr
                return addr
        except Exception as e:
            print(f"[TWAK WARNING] Failed to get address from TWAK: {e}")
        
        # fallback to burner if unable to read from TWAK
        return BURNER_ADDRESS

    async def get_balance(self, token: str = "BNB") -> Decimal:
        address = await self.get_address()
        
        if self.simulation:
            # Check if burner address has real balances on BSC
            try:
                if token.upper() == "BNB":
                    from web3 import Web3
                    w3 = Web3(Web3.HTTPProvider(self.settings.bsc_rpc_url))
                    onchain_bal = w3.eth.get_balance(w3.to_checksum_address(address))
                    bal_val = float(onchain_bal) / 10**18
                    if bal_val > 0:
                        return Decimal(str(bal_val))
                    return Decimal(str(self._sim_bnb_balance))
                
                elif token.upper() == "USDT" or token == self.settings.usdt_contract:
                    onchain_bal = get_balance_of(self.settings.usdt_contract, address)
                    if onchain_bal > 0:
                        return Decimal(str(onchain_bal))
                    return Decimal(str(self._sim_usdt_balance))
                
                else:
                    # Generic token contract or symbol
                    contract_addr = token
                    if not token.startswith("0x"):
                        # If a symbol is passed, we will check our in-memory simulated balances
                        return Decimal(str(self._sim_token_balances.get(token.upper(), 0.0)))
                    onchain_bal = get_balance_of(contract_addr, address)
                    if onchain_bal > 0:
                        return Decimal(str(onchain_bal))
                    return Decimal(str(self._sim_token_balances.get(contract_addr.lower(), 0.0)))
            except Exception:
                # Fallback to simulated local values if RPC queries fail
                if token.upper() == "BNB":
                    return Decimal(str(self._sim_bnb_balance))
                elif token.upper() == "USDT":
                    return Decimal(str(self._sim_usdt_balance))
                else:
                    return Decimal(str(self._sim_token_balances.get(token.lower() if token.startswith("0x") else token.upper(), 0.0)))

        # Live mode
        try:
            # TWAK wallet balance returns standard structure
            data = await self._run_twak(["wallet", "balance", "--chain", "bsc"])
            if token.upper() == "BNB":
                available = data.get("total", data.get("available", 0.0))
                return Decimal(str(available))
            
            # Find in tokens array
            tokens = data.get("tokens", [])
            for t in tokens:
                addr = (t.get("contract") or t.get("address") or "").lower()
                sym = (t.get("symbol") or "").upper()
                balance = t.get("balance", t.get("amount", 0.0))
                
                if token.startswith("0x"):
                    if addr == token.lower():
                        return Decimal(str(balance))
                else:
                    if sym == token.upper() or addr == token.lower():
                        return Decimal(str(balance))
            
            # Fall back to RPC read if TWAK doesn't index it
            if token.startswith("0x"):
                rpc_bal = get_balance_of(token, address)
                return Decimal(str(rpc_bal))
                
            return Decimal("0.0")
        except Exception as e:
            print(f"[TWAK ERROR] get_balance failed: {e}")
            # Fallback to RPC
            if token.startswith("0x"):
                try:
                    return Decimal(str(get_balance_of(token, address)))
                except Exception:
                    pass
            return Decimal("0.0")

    async def quote(self, token_in: str, token_out: str, amount_in: Decimal) -> Quote:
        # TWAK swap has --quote-only flag
        # If we are in simulation, we use mock price of 1.0 or whatever reference price
        if self.simulation:
            # Return a default quote
            now = datetime.now(timezone.utc)
            price = 1.0 # default placeholder price, we will override this with actual CMC quotes in pipeline.py
            return Quote(
                symbol="USDT",
                price=price,
                pct_1h=0.0,
                pct_24h=0.0,
                volume_24h=0.0,
                market_cap=0.0,
                last_updated=now
            )
        
        try:
            data = await self._run_twak([
                "swap", str(amount_in), token_in, token_out,
                "--chain", "bsc", "--quote-only"
            ])
            # Parse output
            price_out = float(data.get("price") or data.get("output") or 0.0)
            return Quote(
                symbol="",
                price=price_out,
                pct_1h=0.0,
                pct_24h=0.0,
                volume_24h=0.0,
                market_cap=0.0,
                last_updated=datetime.now(timezone.utc)
            )
        except Exception as e:
            raise TwakError(f"Quote failed: {e}")

    async def swap(self, token_in: str, token_out: str, amount_in: Decimal,
                   min_out: Decimal, reason: str) -> ExecutionResult:
        if self.simulation:
            # Simulate trade. Assume swap is successful
            tx_id = f"SIMULATED:{uuid.uuid4()}"
            
            # In simulation mode, the execution uses the reference rate but we need to compute simulated balance updates
            # Let's say we receive min_out or simulated output based on a simulated price.
            # If token_in is USDT, we subtract amount_in from USDT, and add to token_out
            # If token_out is USDT, we subtract amount_in from token_in, and add to USDT
            amount_in_f = float(amount_in)
            amount_out_f = float(min_out)
            
            # Update simulated balances
            if token_in.lower() == self.settings.usdt_contract.lower():
                self._sim_usdt_balance = max(0.0, self._sim_usdt_balance - amount_in_f)
                self._sim_token_balances[token_out.lower()] = self._sim_token_balances.get(token_out.lower(), 0.0) + amount_out_f
            elif token_out.lower() == self.settings.usdt_contract.lower():
                self._sim_token_balances[token_in.lower()] = max(0.0, self._sim_token_balances.get(token_in.lower(), 0.0) - amount_in_f)
                self._sim_usdt_balance += amount_out_f
            
            # Compute simulated execution price
            exec_price = amount_out_f / amount_in_f if amount_in_f > 0 else 0.0
            
            return ExecutionResult(
                success=True,
                tx_hash=tx_id,
                executed_price=exec_price,
                amount_in=amount_in_f,
                amount_out=amount_out_f,
                status="confirmed",
                error=None
            )
        
        # Live mode swap execution
        slippage_bps = self.settings.slippage_bps_spot
        if "news" in reason.lower():
            slippage_bps = self.settings.slippage_bps_news
            
        slippage_pct = slippage_bps / 100.0  # e.g. 150 bps -> 1.5%
        
        try:
            data = await self._run_twak([
                "swap", str(amount_in), token_in, token_out,
                "--chain", "bsc",
                "--slippage", str(slippage_pct),
                "--password", self.password
            ], timeout=60)
            
            tx = (data.get("hash") or data.get("txHash") or data.get("transactionHash")
                  or data.get("tx") or "")
            out_amt = float(data.get("output") or data.get("toAmount") or data.get("received") or 0.0)
            
            success = bool(tx) or out_amt > 0.0
            
            # Compute price
            in_amt_f = float(amount_in)
            exec_price = out_amt / in_amt_f if success and in_amt_f > 0 else 0.0
            
            return ExecutionResult(
                success=success,
                tx_hash=tx,
                executed_price=exec_price,
                amount_in=in_amt_f,
                amount_out=out_amt,
                status="confirmed" if success else "reverted",
                error=data.get("error") if not success else None
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                tx_hash="",
                executed_price=0.0,
                amount_in=float(amount_in),
                amount_out=0.0,
                status="reverted",
                error=str(e)
            )

    async def register_for_competition(self) -> str:
        if self.simulation:
            return "SIMULATED_REGISTRATION_SUCCESS"
        
        try:
            data = await self._run_twak(["compete", "register", "--password", self.password], timeout=60)
            if data and not data.get("error"):
                tx = data.get("txHash") or data.get("hash") or "SUCCESS"
                return tx
            raise TwakError((data or {}).get("error", "No registration response"))
        except Exception as e:
            raise TwakError(f"Registration failed: {e}")
