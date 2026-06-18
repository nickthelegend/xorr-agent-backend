import io
import base64
import qrcode
from decimal import Decimal
from typing import Dict, List, Any
from config import settings
from core.twak_executor import TwakExecutor
from core.rpc import get_balance_of
from data.tokens import resolve

GAS_THRESHOLD_BNB = 0.005

class WalletManager:
    def __init__(self, executor: TwakExecutor):
        self.executor = executor

    async def get_address(self) -> str:
        return await self.executor.get_address()

    def generate_qr_base64(self, address: str) -> str:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(address)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        return f"data:image/png;base64,{img_str}"

    async def get_state(self, force_refresh: bool = False) -> Dict[str, Any]:
        address = await self.get_address()
        
        # Get BNB and USDT balance
        bnb_balance = float(await self.executor.get_balance("BNB"))
        usdt_balance = float(await self.executor.get_balance("USDT"))
        
        # For simulation mode or live mode token balances:
        balances_list = []
        
        # BNB Price USD (we will use a fallback of $600 or fetch from settings / engine if we can)
        # Let's see: BNB price is usually around $600. We will set a default or try to get it.
        bnb_price = 600.0
        
        balances_list.append({
            "symbol": "BNB",
            "amount": bnb_balance,
            "usd": round(bnb_balance * bnb_price, 2)
        })
        
        balances_list.append({
            "symbol": "USDT",
            "amount": usdt_balance,
            "usd": round(usdt_balance, 2),
            "contract": settings.usdt_contract
        })
        
        # In simulation mode: include open paper positions valued at live price
        if self.executor.simulation:
            from core import sim_ledger
            from data.cmc_client import _cmc_quotes_cache
            for pos in sim_ledger.list_open_positions():
                if pos.size <= 0:
                    continue
                q = _cmc_quotes_cache.get(pos.symbol.upper())
                usd = pos.size * q.price if (q and q.price > 0) else pos.invested
                balances_list.append({
                    "symbol": pos.symbol,
                    "amount": pos.size,
                    "usd": round(usd, 2),
                    "contract": pos.contract
                })
        else:
            # Live mode: fetch other balances via TWAK if possible
            try:
                # Run `wallet balance` via executor
                data = await self.executor._run_twak(["wallet", "balance", "--chain", "bsc"])
                tokens = data.get("tokens", [])
                for t in tokens:
                    addr = (t.get("contract") or t.get("address") or "").lower()
                    if addr == settings.usdt_contract.lower():
                        continue  # already added
                    
                    qty = float(t.get("balance", t.get("amount", 0.0)))
                    if qty > 0:
                        token_info = resolve(addr)
                        if token_info:
                            balances_list.append({
                                "symbol": token_info.symbol,
                                "amount": qty,
                                "usd": round(qty * float(t.get("priceUsd", t.get("price", 1.0))), 2),
                                "contract": token_info.contract
                            })
            except Exception as e:
                print(f"[WALLET WARNING] Failed to retrieve on-chain token balances: {e}")

        # Check gas status (of the active portfolio view)
        gas_ok = bnb_balance >= GAS_THRESHOLD_BNB

        # Real on-chain snapshot of the self-custody agent wallet — shown in BOTH
        # modes so the operator can fund it and verify holdings before going live.
        onchain = {"address": address, "bnb": 0.0, "bnbUsd": 0.0, "usdt": 0.0, "gasOk": False}
        try:
            from core.agent_wallet import get_agent_wallet
            from data.cmc_client import _cmc_quotes_cache
            w = get_agent_wallet()
            bnb_q = _cmc_quotes_cache.get("BNB")
            bnb_px = bnb_q.price if (bnb_q and bnb_q.price > 0) else bnb_price
            real_bnb = w.bnb_balance()
            real_usdt = w.token_balance(settings.usdt_contract)
            onchain = {
                "address": w.address or address,
                "bnb": real_bnb,
                "bnbUsd": round(real_bnb * bnb_px, 2),
                "usdt": round(real_usdt, 2),
                "gasOk": real_bnb >= GAS_THRESHOLD_BNB,
            }
        except Exception as e:
            print(f"[WALLET WARNING] on-chain snapshot failed: {e}")

        return {
            "address": address,
            "network": "bsc-mainnet" if settings.bsc_chain_id == 56 else "bsc-testnet",
            "balances": balances_list,
            "gasOk": gas_ok,
            "gasThresholdBnb": GAS_THRESHOLD_BNB,
            "simulation": self.executor.simulation,
            "onchain": onchain,
            "qrPngBase64": self.generate_qr_base64(address)
        }
