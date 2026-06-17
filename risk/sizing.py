from config import settings

def calculate_trade_size(
    fear_greed_value: int,
    drawdown_multiplier: float,
    strategy_name: str,
    available_usdt: float,
    active_position_count: int
) -> float:
    """
    Computes size in USDT for a trade based on Fear & Greed index, drawdown status,
    and strategy types. Caps sizing at available USDT and concurrent limits.
    """
    if active_position_count >= settings.max_concurrent_positions:
        print(f"[SIZING] Rejected: Active positions ({active_position_count}) >= max limit ({settings.max_concurrent_positions}).")
        return 0.0
        
    # 1. Fear and Greed Multiplier
    # 0.5 if F&G < 20, 0.75 if < 40, 1.0 if 40–70, 0.8 if > 70, 0.5 if > 85.
    if fear_greed_value < 20:
        fg_mult = 0.5
    elif fear_greed_value < 40:
        fg_mult = 0.75
    elif fear_greed_value <= 70:
        fg_mult = 1.0
    elif fear_greed_value <= 85:
        fg_mult = 0.8
    else:
        # F&G > 85
        fg_mult = 0.5
        
    # 2. Strategy Multiplier
    # 1.0 default; 2.0 for news_catalyst; 0.8 for capitulation.
    strat_name_lower = strategy_name.lower()
    if "news" in strat_name_lower:
        strat_mult = 2.0
    elif "capitulation" in strat_name_lower:
        strat_mult = 0.8
    else:
        strat_mult = 1.0
        
    # Calculate target trade size
    size_usd = settings.base_trade_size_usd * fg_mult * drawdown_multiplier * strat_mult
    
    # Cap size to available USDT balance
    if size_usd > available_usdt:
        size_usd = available_usdt
        
    # Enforce a minimum trade size of $1.10 to prevent dust order errors on-chain
    if size_usd < 1.10:
        print(f"[SIZING] Target size ${size_usd:.2f} is below minimum allowed trade ($1.10). Aborting trade.")
        return 0.0
        
    print(f"[SIZING] Calculated trade size: ${size_usd:.2f} (Base=${settings.base_trade_size_usd}, F&G={fear_greed_value} ({fg_mult}x), Drawdown={drawdown_multiplier}x, Strategy={strat_name_lower} ({strat_mult}x))")
    return round(size_usd, 2)
