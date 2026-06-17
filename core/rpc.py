from web3 import Web3
from typing import Dict
from config import settings

# Minimal ERC20 ABI for decimals, balanceOf, allowance
MIN_ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"}
        ],
        "name": "allowance",
        "outputs": [{"name": "remaining", "type": "uint256"}],
        "type": "function",
    }
]

# Simple in-memory cache for decimals to save RPC requests
_decimals_cache: Dict[str, int] = {}

def get_w3() -> Web3:
    return Web3(Web3.HTTPProvider(settings.bsc_rpc_url))

def get_decimals(token_address: str) -> int:
    w3 = get_w3()
    checksum_addr = w3.to_checksum_address(token_address)
    if checksum_addr in _decimals_cache:
        return _decimals_cache[checksum_addr]
    
    try:
        contract = w3.eth.contract(address=checksum_addr, abi=MIN_ERC20_ABI)
        dec = contract.functions.decimals().call()
        _decimals_cache[checksum_addr] = dec
        return dec
    except Exception as e:
        print(f"[RPC WARNING] Failed to fetch decimals for {token_address}: {e}")
        # Default to 18 if call fails
        return 18

def get_balance_of(token_address: str, wallet_address: str) -> float:
    w3 = get_w3()
    try:
        checksum_token = w3.to_checksum_address(token_address)
        checksum_wallet = w3.to_checksum_address(wallet_address)
        contract = w3.eth.contract(address=checksum_token, abi=MIN_ERC20_ABI)
        raw_balance = contract.functions.balanceOf(checksum_wallet).call()
        dec = get_decimals(checksum_token)
        return float(raw_balance) / (10 ** dec)
    except Exception as e:
        print(f"[RPC WARNING] Failed to fetch balance for {token_address} of {wallet_address}: {e}")
        return 0.0

def get_allowance(token_address: str, owner_address: str, spender_address: str) -> float:
    w3 = get_w3()
    try:
        checksum_token = w3.to_checksum_address(token_address)
        checksum_owner = w3.to_checksum_address(owner_address)
        checksum_spender = w3.to_checksum_address(spender_address)
        contract = w3.eth.contract(address=checksum_token, abi=MIN_ERC20_ABI)
        raw_allowance = contract.functions.allowance(checksum_owner, checksum_spender).call()
        dec = get_decimals(checksum_token)
        return float(raw_allowance) / (10 ** dec)
    except Exception as e:
        print(f"[RPC WARNING] Failed to fetch allowance: {e}")
        return 0.0

def get_block_number() -> int:
    w3 = get_w3()
    try:
        return w3.eth.block_number
    except Exception as e:
        print(f"[RPC WARNING] Failed to fetch block number: {e}")
        return 0
