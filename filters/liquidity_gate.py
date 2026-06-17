import random
from config import settings
from core.twak_executor import TwakExecutor
from data.tokens import resolve

async def passes_liquidity_gate(executor: TwakExecutor, symbol: str, spot_price: float) -> bool:
    """
    Performs price impact checks. Swaps base USDT size to candidate token.
    If implied slippage exceeds settings.liquidity_impact_bps, rejects candidate.
    """
    if spot_price <= 0:
        return False
        
    token_info = resolve(symbol)
    if not token_info:
        return False
        
    contract_address = token_info.contract
    base_usd = settings.base_trade_size_usd

    # In simulation mode, we mock the liquidity check or simulate price impact
    if executor.simulation:
        # Simulate price impact: random slippage between 10 to 60 basis points
        sim_slippage_bps = random.uniform(10, 60)
        if sim_slippage_bps > settings.liquidity_impact_bps:
            print(f"[LIQUIDITY REJECT] {symbol} simulated slippage {sim_slippage_bps:.1f} bps exceeds limit {settings.liquidity_impact_bps} bps")
            return False
        return True

    # Live Mode
    try:
        from decimal import Decimal
        # Get quote USDT -> token
        quote_data = await executor.quote(settings.usdt_contract, contract_address, Decimal(str(base_usd)))
        tokens_received = quote_data.price # TwakExecutor.quote returns received tokens amount as .price
        
        if tokens_received <= 0:
            print(f"[LIQUIDITY REJECT] {symbol} quote returned 0 tokens received.")
            return False
            
        effective_price = base_usd / tokens_received
        
        # Spot price: 1 token = spot_price USDT
        # Effective price: 1 token = effective_price USDT
        # Slippage: (effective_price - spot_price) / spot_price
        slippage_pct = (effective_price - spot_price) / spot_price
        slippage_bps = slippage_pct * 10000.0
        
        if slippage_bps > settings.liquidity_impact_bps:
            print(f"[LIQUIDITY REJECT] {symbol} price impact of {slippage_bps:.1f} bps exceeds limit {settings.liquidity_impact_bps} bps (Base size=${base_usd})")
            return False
            
        return True
    except Exception as e:
        print(f"[LIQUIDITY WARNING] Failed to execute quote for {symbol}: {e}. Fails open.")
        return True
