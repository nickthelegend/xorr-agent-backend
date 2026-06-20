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
from core import perp_math

# Simulated fill costs (paper trading): PancakeSwap-style swap fee + realized price
# impact. NOTE: settings.slippage_bps_* is the slippage TOLERANCE (min-out guard for
# the live swap), not the realized fill cost — using it as the fill cost overcharges
# paper trades ~1.5%/leg. A $1-2 swap of a liquid token realizes ~10 bps impact.
SIM_SWAP_FEE = 0.0025          # 0.25% PancakeSwap pool fee per leg
SIM_REALIZED_SLIPPAGE = 0.001  # ~10 bps realized impact per leg (liquid spot)
SIM_REALIZED_SLIPPAGE_NEWS = 0.003  # wider for news/illiquid entries
# Perp taker fee per side (Aster/Hyperliquid-class). Charged on notional, not margin.
SIM_PERP_TAKER_FEE = 0.0006    # 6 bps/side

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

    def _twak_ready(self) -> bool:
        """True when the real TWAK CLI is installed AND authenticated — either via env
        creds OR a persisted ~/.twak/credentials.json (written by `twak init`/`twak setup`).
        Otherwise the agent uses the local web3 self-custody keystore."""
        cli = shutil.which(self.settings.twak_bin)
        if not cli:
            return False
        access = os.environ.get("TWAK_ACCESS_ID", self.settings.twak_access_id)
        secret = os.environ.get("TWAK_HMAC_SECRET", self.settings.twak_hmac_secret)
        if access and secret:
            return True
        try:
            from pathlib import Path
            return (Path.home() / ".twak" / "credentials.json").exists()
        except Exception:
            return False

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
        # Trust Wallet API credentials (required by the real CLI)
        access = os.environ.get("TWAK_ACCESS_ID", self.settings.twak_access_id)
        secret = os.environ.get("TWAK_HMAC_SECRET", self.settings.twak_hmac_secret)
        if access:
            env["TWAK_ACCESS_ID"] = access
        if secret:
            env["TWAK_HMAC_SECRET"] = secret

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
        if self._cached_address:
            return self._cached_address

        # On the competition machine, prefer the TWAK-managed wallet when the CLI
        # is installed (it owns its own self-custody key).
        if not self.simulation and self._twak_ready():
            try:
                data = await self._run_twak(["wallet", "address", "--chain", "bsc"])
                addr = data.get("address") or data.get("0") or ""
                if not addr:
                    # No wallet yet — create one (TWAK manages the key, encrypted)
                    await self._run_twak(["wallet", "create", "--password", self.password,
                                          "--no-keychain", "--skip-password-check"], timeout=60)
                    data = await self._run_twak(["wallet", "address", "--chain", "bsc"])
                    addr = data.get("address") or data.get("0") or ""
                if addr:
                    self._cached_address = addr
                    return addr
            except Exception as e:
                print(f"[TWAK WARNING] Failed to get address from TWAK CLI: {e}")

        # Otherwise use the real local self-custody keystore wallet (sign locally).
        try:
            from core.agent_wallet import get_agent_wallet
            w = get_agent_wallet()
            if w.address:
                self._cached_address = w.address
                return w.address
        except Exception as e:
            print(f"[WALLET WARNING] keystore wallet unavailable: {e}")

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

        # Live mode — read real on-chain balances directly via web3 when TWAK is
        # not fully configured (self-custody keystore wallet).
        if not self._twak_ready():
            try:
                from core.agent_wallet import get_agent_wallet
                w = get_agent_wallet()
                if token.upper() == "BNB":
                    return Decimal(str(w.bnb_balance()))
                if token.upper() == "USDT" or token.lower() == self.settings.usdt_contract.lower():
                    return Decimal(str(w.token_balance(self.settings.usdt_contract)))
                contract_addr = token
                if not token.startswith("0x"):
                    from data.tokens import resolve
                    tok = resolve(token)
                    contract_addr = tok.contract if tok else token
                return Decimal(str(w.token_balance(contract_addr)))
            except Exception as e:
                print(f"[WALLET ERROR] on-chain balance read failed: {e}")
                return Decimal("0.0")

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

            # Realistic realized fill cost per leg (fee + impact), NOT the tolerance
            realized_slip = SIM_REALIZED_SLIPPAGE_NEWS if "news" in reason.lower() else SIM_REALIZED_SLIPPAGE
            cost = SIM_SWAP_FEE + realized_slip

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

        # If TWAK is not fully configured (CLI + credentials), execute on-chain
        # directly by signing locally with the self-custody keystore wallet (web3).
        if not self._twak_ready():
            try:
                from core.agent_wallet import get_agent_wallet
                w = get_agent_wallet()
                dec_in = get_decimals(token_in)
                dec_out = get_decimals(token_out)
                amount_in_wei = int(Decimal(str(amount_in)) * (Decimal(10) ** dec_in))
                min_out_wei = int(Decimal(str(min_out)) * (Decimal(10) ** dec_out))
                res = w.swap_tokens(token_in, token_out, amount_in_wei, min_out_wei)
                usdt = self.settings.usdt_contract.lower()
                if token_in.lower() == usdt:
                    exec_price = (res.amount_in / res.amount_out) if res.amount_out > 0 else 0.0
                else:
                    exec_price = (res.amount_out / res.amount_in) if res.amount_in > 0 else 0.0
                return ExecutionResult(
                    success=res.success, tx_hash=res.tx_hash, executed_price=exec_price,
                    amount_in=res.amount_in, amount_out=res.amount_out,
                    status="confirmed" if res.success else "reverted", error=res.error,
                )
            except Exception as e:
                return ExecutionResult(
                    success=False, tx_hash="", executed_price=0.0,
                    amount_in=float(amount_in), amount_out=0.0, status="reverted", error=str(e),
                )

        slippage_bps = self.settings.slippage_bps_spot
        if "news" in reason.lower():
            slippage_bps = self.settings.slippage_bps_news

        slippage_pct = slippage_bps / 100.0  # e.g. 150 bps -> 1.5%

        try:
            # Verified spot form (tw-agent-skills references/swap.md, mirrored in
            # trading-agent/venues.py): `twak swap <AMOUNT> <FROM> <TO> --chain bsc
            # --slippage <pct>`. AMOUNT is in FROM-token units (USDT for a buy,
            # token units for a sell) — exactly what callers pass as amount_in.
            # The wallet password is supplied via the TWAK_WALLET_PASSWORD env var
            # in _run_twak, NOT as a CLI arg (secrets never go on argv).
            data = await self._run_twak([
                "swap", str(amount_in), token_in, token_out,
                "--chain", "bsc",
                "--slippage", str(slippage_pct),
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

    # ------------------------------------------------------------------
    # Perpetual futures (BSC perps via TWAK -> Aster/Hyperliquid). LONG & SHORT.
    # Live form (trading-agent/venues.py, verified):
    #   twak perps open <SYMBOL> --side long|short --usd <MARGIN> --leverage <L> --chain bsc
    #   twak perps close <SYMBOL> --chain bsc
    #   twak perps mark  <SYMBOL> --chain bsc
    # NOTE: we treat `--usd` as the MARGIN (collateral) to commit; leverage sets
    # the notional. settings.perp_usd_is_margin lets the operator flip this if a
    # given twak build reads --usd as notional. VERIFY with one dust perp on the
    # competition machine (open $1 margin, run `twak perps positions`).
    # ------------------------------------------------------------------
    async def open_perp(self, symbol: str, direction: str, margin_usd: Decimal,
                        leverage: float, ref_price: Optional[float] = None,
                        reason: str = "PERP_ENTRY") -> ExecutionResult:
        direction = "short" if str(direction).lower() == "short" else "long"
        margin_f = float(margin_usd)

        # Hard safety: in the spot-only competition we NEVER open a perp, even if a
        # perp signal somehow reaches here. Fail closed.
        if bool(getattr(self.settings, "spot_only", False)):
            return ExecutionResult(success=False, tx_hash="", executed_price=0.0,
                                   amount_in=margin_f, amount_out=0.0, status="reverted",
                                   error="spot_only mode: perp trading is disabled")

        if self.simulation:
            sim_ledger.ensure_seeded()
            price = float(ref_price) if ref_price and ref_price > 0 else self._lookup_sim_price(symbol, symbol)
            if price <= 0.0:
                return ExecutionResult(success=False, tx_hash="", executed_price=0.0,
                                       amount_in=margin_f, amount_out=0.0, status="reverted",
                                       error="No reference price for simulated perp")
            size = perp_math.notional_units(margin_f, leverage, price)
            notional = margin_f * leverage
            fee = notional * SIM_PERP_TAKER_FEE
            # Lock margin + pay the entry taker fee out of paper cash.
            sim_ledger.adjust_cash(-(margin_f + fee))
            tx_id = f"SIMPERP:{uuid.uuid4()}"
            return ExecutionResult(success=True, tx_hash=tx_id, executed_price=price,
                                   amount_in=margin_f, amount_out=size, status="confirmed", error=None)

        # Live — perps require the real TWAK CLI + credentials (no web3 fallback:
        # Aster/HL perps are not a simple router call). Fail clearly so the agent
        # gracefully keeps trading spot when perps are unavailable.
        if not self._twak_ready():
            return ExecutionResult(success=False, tx_hash="", executed_price=0.0,
                                   amount_in=margin_f, amount_out=0.0, status="reverted",
                                   error="Perps unavailable: TWAK CLI + credentials required")
        usd_arg = margin_f
        if not getattr(self.settings, "perp_usd_is_margin", True):
            usd_arg = margin_f * leverage  # this build reads --usd as notional
        try:
            args = ["perps", "open", symbol, "--side", direction,
                    "--usd", f"{usd_arg:.2f}", "--leverage", str(leverage), "--chain", "bsc"]
            data = await self._run_twak(args, timeout=90)
            if data.get("error"):
                return ExecutionResult(success=False, tx_hash="", executed_price=0.0,
                                       amount_in=margin_f, amount_out=0.0, status="reverted",
                                       error=str(data.get("error"))[:300])
            tx = (data.get("txHash") or data.get("hash") or data.get("positionId") or "")
            entry = float(data.get("entryPrice") or data.get("price") or (ref_price or 0.0))
            size = float(data.get("size") or data.get("positionSize") or 0.0)
            if size <= 0 and entry > 0:
                size = perp_math.notional_units(margin_f, leverage, entry)
            success = bool(tx) or size > 0
            return ExecutionResult(success=success, tx_hash=tx or "", executed_price=entry,
                                   amount_in=margin_f, amount_out=size,
                                   status="confirmed" if success else "reverted",
                                   error=None if success else "perp open returned no id")
        except Exception as e:
            return ExecutionResult(success=False, tx_hash="", executed_price=0.0,
                                   amount_in=margin_f, amount_out=0.0, status="reverted", error=str(e))

    async def close_perp(self, symbol: str, direction: str, size_units: float,
                         entry_price: float, margin_usd: float, leverage: float,
                         ref_price: Optional[float] = None, hold_hours: float = 0.0) -> ExecutionResult:
        """Closes a perp. amount_out = USDT credited back (margin + realized PnL
        - fees - funding carry), so the caller computes realized PnL as
        amount_out - margin, the same convention as a spot sell. hold_hours drives
        the funding carry (perps charge funding ~every 8h; long holds bleed it)."""
        direction = "short" if str(direction).lower() == "short" else "long"

        if self.simulation:
            sim_ledger.ensure_seeded()
            price = float(ref_price) if ref_price and ref_price > 0 else self._lookup_sim_price(symbol, symbol)
            if price <= 0.0:
                return ExecutionResult(success=False, tx_hash="", executed_price=0.0,
                                       amount_in=size_units, amount_out=0.0, status="reverted",
                                       error="No reference price for simulated perp close")
            upnl = perp_math.unrealized_pnl(direction, size_units, entry_price, price)
            fee = (size_units * price) * SIM_PERP_TAKER_FEE
            # Funding carry over the hold (conservative: a small always-cost so sim
            # doesn't over-reward multi-hour/day perp holds).
            funding_rate = float(getattr(self.settings, "perp_funding_rate_8h", 0.0001))
            funding = (size_units * price) * funding_rate * (max(0.0, hold_hours) / 8.0)
            proceeds = max(0.0, float(margin_usd) + upnl - fee - funding)
            sim_ledger.adjust_cash(proceeds)
            tx_id = f"SIMPERP:close:{uuid.uuid4()}"
            return ExecutionResult(success=True, tx_hash=tx_id, executed_price=price,
                                   amount_in=size_units, amount_out=proceeds, status="confirmed", error=None)

        if not self._twak_ready():
            return ExecutionResult(success=False, tx_hash="", executed_price=0.0,
                                   amount_in=size_units, amount_out=0.0, status="reverted",
                                   error="Perps unavailable: TWAK CLI + credentials required")
        try:
            data = await self._run_twak(["perps", "close", symbol, "--chain", "bsc"], timeout=90)
            if data.get("error"):
                return ExecutionResult(success=False, tx_hash="", executed_price=0.0,
                                       amount_in=size_units, amount_out=0.0, status="reverted",
                                       error=str(data.get("error"))[:300])
            tx = (data.get("txHash") or data.get("hash") or "")
            close_px = float(data.get("closePrice") or data.get("exitPrice") or data.get("price") or (ref_price or 0.0))
            # Prefer venue-reported realized PnL; else compute from close price.
            realized = data.get("realizedPnl") or data.get("pnl")
            if realized is not None:
                proceeds = max(0.0, float(margin_usd) + float(realized))
            else:
                upnl = perp_math.unrealized_pnl(direction, size_units, entry_price, close_px)
                proceeds = max(0.0, float(margin_usd) + upnl)
            success = bool(tx) or close_px > 0
            return ExecutionResult(success=success, tx_hash=tx or "", executed_price=close_px,
                                   amount_in=size_units, amount_out=proceeds,
                                   status="confirmed" if success else "reverted",
                                   error=None if success else "perp close returned no id")
        except Exception as e:
            return ExecutionResult(success=False, tx_hash="", executed_price=0.0,
                                   amount_in=size_units, amount_out=0.0, status="reverted", error=str(e))

    async def perp_mark(self, symbol: str) -> Optional[float]:
        """Live perp mark price (the price the venue marks/liquidates against),
        or None when unavailable (sim, no CLI, or build lacks the surface)."""
        if self.simulation or not self._twak_ready():
            return None
        try:
            data = await self._run_twak(["perps", "mark", symbol, "--chain", "bsc"], timeout=30)
            if not isinstance(data, dict) or data.get("error"):
                return None
            raw = data.get("markPrice") or data.get("mark") or data.get("price")
            px = float(raw)
            return px if px > 0 else None
        except Exception:
            return None

    async def list_perp_positions(self) -> Optional[List[Dict[str, Any]]]:
        """On-chain open perp positions for boot reconciliation. Returns a list of
        dicts (one per open perp), or None when UNVERIFIABLE (sim mode, no CLI, or
        the twak build lacks the surface) — callers must treat None as "don't
        touch" (fail-safe: never close a local perp on uncertain data)."""
        if self.simulation or not self._twak_ready():
            return None
        try:
            data = await self._run_twak(["perps", "positions", "--chain", "bsc"], timeout=30)
            if not isinstance(data, dict) or data.get("error"):
                return None
            positions = data.get("positions") or data.get("data") or []
            return positions if isinstance(positions, list) else None
        except Exception:
            return None

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
