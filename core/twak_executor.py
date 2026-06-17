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
from core import sim_ledger

# Simulated fill costs (paper trading): PancakeSwap-style swap fee + price impact.
SIM_SWAP_FEE = 0.0025  # 0.25% per swap leg

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
            # In simulation the persistent paper ledger (DB) is the source of truth.
            # USDT and BNB come from the ledger; token balances are the sum of open
            # position sizes for that contract.
            sim_ledger.ensure_seeded()
            if token.upper() == "BNB":
                return Decimal(str(sim_ledger.get_bnb()))
            if token.upper() == "USDT" or token.lower() == self.settings.usdt_contract.lower():
                return Decimal(str(sim_ledger.get_cash()))
            # Generic token: resolve to a contract address then sum open positions
            contract_addr = token
            if not token.startswith("0x"):
                from data.tokens import resolve
                tok = resolve(token)
                contract_addr = tok.contract if tok else token
            return Decimal(str(sim_ledger.get_token_balance(contract_addr)))

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

    def _lookup_sim_price(self, token_in: str, token_out: str) -> float:
        """Best-effort USD price of the non-USDT leg from the cached CMC/Binance
        quotes. Used as a fallback when callers don't pass an explicit ref_price."""
        usdt = self.settings.usdt_contract.lower()
        other = token_out if token_in.lower() == usdt else token_in
        try:
            from data.tokens import resolve
            from data.cmc_client import _cmc_quotes_cache
            tok = resolve(other)
            symbol = tok.symbol.upper() if tok else other.upper()
            q = _cmc_quotes_cache.get(symbol)
            if q and q.price > 0:
                return float(q.price)
        except Exception:
            pass
        return 0.0

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
                   min_out: Decimal, reason: str, ref_price: Optional[float] = None) -> ExecutionResult:
        if self.simulation:
            # Paper trade against the real reference price (USD price of the
            # non-USDT leg), applying swap fee + slippage. The executed_price we
            # return is the true USD price so position valuation/PnL is correct.
            sim_ledger.ensure_seeded()
            tx_id = f"SIMULATED:{uuid.uuid4()}"
            amount_in_f = float(amount_in)
            usdt = self.settings.usdt_contract.lower()

            # Slippage cost in fractional terms (news entries get a wider band)
            slip_bps = self.settings.slippage_bps_news if "news" in reason.lower() else self.settings.slippage_bps_spot
            cost = SIM_SWAP_FEE + (slip_bps / 10000.0)

            price = float(ref_price) if ref_price and ref_price > 0 else self._lookup_sim_price(token_in, token_out)

            if price <= 0.0:
                return ExecutionResult(
                    success=False, tx_hash="", executed_price=0.0,
                    amount_in=amount_in_f, amount_out=0.0, status="reverted",
                    error="No reference price available for simulated swap",
                )

            if token_in.lower() == usdt:
                # BUY: spend USDT, receive token units
                usable_usd = amount_in_f * (1.0 - cost)
                tokens_out = usable_usd / price
                sim_ledger.adjust_cash(-amount_in_f)
                return ExecutionResult(
                    success=True, tx_hash=tx_id, executed_price=price,
                    amount_in=amount_in_f, amount_out=tokens_out,
                    status="confirmed", error=None,
                )
            elif token_out.lower() == usdt:
                # SELL: spend token units, receive USDT
                gross_usd = amount_in_f * price
                usd_out = gross_usd * (1.0 - cost)
                sim_ledger.adjust_cash(usd_out)
                return ExecutionResult(
                    success=True, tx_hash=tx_id, executed_price=price,
                    amount_in=amount_in_f, amount_out=usd_out,
                    status="confirmed", error=None,
                )
            else:
                # token -> token (not used in the current pipeline); pass-through
                return ExecutionResult(
                    success=True, tx_hash=tx_id, executed_price=price,
                    amount_in=amount_in_f, amount_out=amount_in_f,
                    status="confirmed", error=None,
                )

        # Live mode swap execution

        # GUARDRAIL: never send real funds into a token outside the competition
        # eligibility whitelist. The non-USDT leg of every swap must be eligible.
        usdt = self.settings.usdt_contract.lower()
        non_usdt_leg = token_out if token_in.lower() == usdt else token_in
        if non_usdt_leg.lower() != usdt:
            from data.tokens import is_eligible
            if not is_eligible(non_usdt_leg):
                return ExecutionResult(
                    success=False, tx_hash="", executed_price=0.0,
                    amount_in=float(amount_in), amount_out=0.0, status="reverted",
                    error=f"Eligibility guardrail: {non_usdt_leg} is not in the competition whitelist",
                )

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
