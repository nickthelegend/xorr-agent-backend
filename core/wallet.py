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
        
        # In simulation mode: include any non-zero simulated token balances
        if self.executor.simulation:
            for token_addr, amt in self.executor._sim_token_balances.items():
                if amt > 0:
                    token_info = resolve(token_addr)
                    symbol = token_info.symbol if token_info else "TOKEN"
                    balances_list.append({
                        "symbol": symbol,
                        "amount": amt,
                        "usd": round(amt, 2),  # Assume $1 for sim usd if we don't have price feed here
                        "contract": token_addr
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

        # Check gas status
        gas_ok = bnb_balance >= GAS_THRESHOLD_BNB

        return {
            "address": address,
            "network": "bsc-mainnet" if settings.bsc_chain_id == 56 else "bsc-testnet",
            "balances": balances_list,
            "gasOk": gas_ok,
            "gasThresholdBnb": GAS_THRESHOLD_BNB,
            "qrPngBase64": self.generate_qr_base64(address)
        }
