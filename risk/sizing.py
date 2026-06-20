from config import settings
from engine.learning import get_weight


def calculate_perp_margin(
    equity: float,
    open_positions: list,
    council_confidence: float,
    n_agree: int,
    drawdown_multiplier: float,
    available_usdt: float,
) -> float:
    """USDT margin to post on a perp, bounded so a single liquidation can never
    threaten the competition's max-drawdown disqualification gate.

    Hard caps:
      - per trade  <= perp_margin_pct_per_trade * equity
      - all perps  <= perp_total_margin_pct   * equity  (remaining room)
      - <= available USDT cash
    Scaled within the per-trade cap by council confidence + ensemble agreement +
    the drawdown multiplier. Floored at the venue minimum, else 0 (skip).
    """
    if equity <= 0:
        return 0.0
    per_cap = settings.perp_margin_pct_per_trade * equity
    total_cap = settings.perp_total_margin_pct * equity
    used = sum(p.invested for p in open_positions if getattr(p, "is_perp", False))
    room = max(0.0, total_cap - used)

    conf_mult = 0.6 + max(0.0, min(1.0, council_confidence))      # 0.6 .. 1.6
    agree_mult = min(1.4, 1.0 + 0.2 * max(0, n_agree - 1))
    base = 0.6 * per_cap
    margin = base * conf_mult * agree_mult * drawdown_multiplier

    margin = min(margin, per_cap, room, available_usdt)

    # Below the venue minimum -> skip (don't up-size a de-risked/low-conviction trade
    # just to clear the floor; that would fight the drawdown ladder).
    if margin < settings.perp_min_margin_usd:
        return 0.0
    return round(margin, 2)

def calculate_claude_size(
    available_usdt: float,
    deployed_usd: float,
    active_position_count: int,
    conviction: float,
    drawdown_multiplier: float = 1.0,
) -> float:
    """Tournament sizing for Claude's force-entered picks — deploy REAL capital into a few
    concentrated bets, capped to protect the ~30% drawdown DQ gate.

    Claude's picks are deliberate, confluence-verified decisions the user wants TAKEN, so
    they bypass the generic multiplier stack — in particular the Fear & Greed penalty, which
    is perverse here (our edge is FADING fear). We size by conviction off the base, then cap:
      - per position  <= claude_max_position_pct of trading capital
      - all positions <= claude_max_deploy_pct  of trading capital (remaining room)
      - <= available USDT cash, and the concurrency + drawdown-ladder gates still bind.
    'Trading capital' = USDT cash + USDT already in open positions (excludes the BNB gas
    reserve), so the % caps reflect what we can actually deploy.
    """
    if active_position_count >= settings.max_concurrent_positions:
        print(f"[SIZING] Claude pick rejected: positions ({active_position_count}) >= max ({settings.max_concurrent_positions}).")
        return 0.0
    if drawdown_multiplier <= 0:
        return 0.0   # drawdown ladder says stand down — respect the DQ gate above all

    trading_capital = max(0.0, available_usdt) + max(0.0, deployed_usd)
    per_pos_cap = float(getattr(settings, "claude_max_position_pct", 0.25)) * trading_capital
    total_cap = float(getattr(settings, "claude_max_deploy_pct", 0.60)) * trading_capital
    room = max(0.0, total_cap - deployed_usd)          # how much more we may deploy in total

    conv = max(0.0, min(1.0, conviction))
    conv_mult = 0.8 + 0.8 * conv                       # 0.8x .. 1.6x of base
    size = settings.base_trade_size_usd * conv_mult * drawdown_multiplier
    size = max(size, 1.20)                             # a real pick clears the dust floor...
    size = min(size, per_pos_cap, room, available_usdt)  # ...but never breaches the caps
    if size < 1.10:
        return 0.0
    print(f"[SIZING] Claude size: ${size:.2f} (base=${settings.base_trade_size_usd}, conv={conv:.2f} ({conv_mult:.2f}x), "
          f"DD={drawdown_multiplier}x, per-pos cap=${per_pos_cap:.2f}, room=${room:.2f})")
    return round(size, 2)


def calculate_trade_size(
    fear_greed_value: int,
    drawdown_multiplier: float,
    strategy_name: str,
    available_usdt: float,
    active_position_count: int,
    council_confidence: float = 1.0,
    council_consensus: float = 0.0,
    n_agree: int = 1
) -> float:
    """
    Computes size in USDT for a trade based on Fear & Greed index, drawdown status,
    strategy types, learning multipliers, and council confidence/consensus.
    Caps sizing at available USDT and concurrent limits.
    """
    if active_position_count >= settings.max_concurrent_positions:
        print(f"[SIZING] Rejected: Active positions ({active_position_count}) >= max limit ({settings.max_concurrent_positions}).")
        return 0.0
        
    # 1. Fear and Greed Multiplier
    if fear_greed_value < 20:
        fg_mult = 0.5
    elif fear_greed_value < 40:
        fg_mult = 0.75
    elif fear_greed_value <= 70:
        fg_mult = 1.0
    elif fear_greed_value <= 85:
        fg_mult = 0.8
    else:
        fg_mult = 0.5
        
    # 2. Strategy Multiplier
    strat_name_lower = strategy_name.lower()
    if "news" in strat_name_lower:
        strat_mult = 2.0
    elif "capitulation" in strat_name_lower:
        strat_mult = 0.8
    else:
        strat_mult = 1.0

    # 3. Learning Multiplier (0.1 .. 2.0)
    learn_mult = get_weight(strategy_name)
    
    # 4. Council Multiplier (0.5 .. 1.5)
    council_mult = 0.5 + council_confidence
    
    # 5. Quality Multiplier
    if not settings.quality_mode:
        quality_mult = 1.0
    else:
        quality_mult = 0.7 if council_consensus > 0.15 else 1.1

    # 6. Ensemble agreement multiplier (more strategies agree -> larger conviction,
    #    capped at +50%)
    agree_mult = min(1.5, 1.0 + 0.25 * max(0, n_agree - 1))

    # Calculate target trade size
    size_usd = settings.base_trade_size_usd * fg_mult * drawdown_multiplier * strat_mult * learn_mult * council_mult * quality_mult * agree_mult
    
    # Cap size to available USDT balance
    if size_usd > available_usdt:
        size_usd = available_usdt
        
    # Enforce a minimum trade size of $1.10 to prevent dust order errors on-chain
    if size_usd < 1.10:
        print(f"[SIZING] Target size ${size_usd:.2f} is below minimum allowed trade ($1.10). Aborting trade.")
        return 0.0
        
    print(f"[SIZING] Calculated trade size: ${size_usd:.2f} (Base=${settings.base_trade_size_usd}, F&G={fear_greed_value} ({fg_mult}x), Drawdown={drawdown_multiplier}x, Strategy={strat_name_lower} ({strat_mult}x), Learn={learn_mult}x, Council={council_mult}x, Quality={quality_mult}x, Agree={n_agree} ({agree_mult}x))")
    return round(size_usd, 2)
