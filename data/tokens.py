import json
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel

# Token representation
class Token(BaseModel):
    symbol: str
    contract: str
    decimals: int
    tradable: bool = True       # has a verified contract + price source (liquid subset)
    binance_symbol: str = ""    # Binance spot base for price/klines, "" if none

_tokens_by_symbol: Dict[str, Token] = {}
_tokens_by_contract: Dict[str, Token] = {}

def load_tokens():
    """Loads whitelisted tokens from JSON file."""
    global _tokens_by_symbol, _tokens_by_contract
    # Path relative to workspace or backend
    json_path = Path(__file__).parent.parent / "tokens.eligible.json"
    if not json_path.exists():
        # fallback to cwd
        json_path = Path("tokens.eligible.json")
    
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    t = Token(
                        symbol=item["symbol"],
                        contract=item.get("contract", ""),
                        decimals=item.get("decimals", 18),
                        tradable=item.get("tradable", bool(item.get("contract"))),
                        binance_symbol=item.get("binance_symbol", "")
                    )
                    _tokens_by_symbol[t.symbol.upper()] = t
                    if t.contract:
                        _tokens_by_contract[t.contract.lower()] = t
            print(f"[TOKENS] Loaded {len(_tokens_by_symbol)} tokens from {json_path}")
        except Exception as e:
            print(f"[TOKENS ERROR] Failed to load whitelisted tokens: {e}")
    else:
        print("[TOKENS WARNING] tokens.eligible.json not found.")

# Initial load on import
load_tokens()

def resolve(key: str) -> Optional[Token]:
    """Resolves a token object by symbol or contract address."""
    if not key:
        return None
    key_clean = key.strip()
    if key_clean.startswith("0x"):
        return _tokens_by_contract.get(key_clean.lower())
    return _tokens_by_symbol.get(key_clean.upper())

def iter_all() -> List[Token]:
    """Returns all eligible tokens (the full competition whitelist)."""
    return list(_tokens_by_symbol.values())


def iter_tradable() -> List[Token]:
    """Returns the liquid subset the agent actively trades (verified contract +
    price source). The full list remains the on-chain eligibility whitelist."""
    return [t for t in _tokens_by_symbol.values() if t.tradable and t.contract]


def binance_symbol_for(symbol: str) -> str:
    """Binance spot base asset for an eligible symbol, or '' if not listed."""
    tok = resolve(symbol)
    return tok.binance_symbol if tok else ""

def is_eligible(key: str) -> bool:
    """Checks if a symbol or contract address is in the whitelist."""
    return resolve(key) is not None
