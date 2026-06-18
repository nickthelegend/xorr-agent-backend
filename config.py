from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional

class Settings(BaseSettings):
    # --- Wallet / Chain ---
    bsc_rpc_url: str = Field(default="https://bsc-dataseed.binance.org")
    bsc_chain_id: int = Field(default=56)
    usdt_contract: str = Field(default="0x55d398326f99059fF775485246999027B3197955")
    wbnb_contract: str = Field(default="0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c")
    pancake_router: str = Field(default="0x10ED43C718714eb63d5aA57B78B54704E256024E")

    # --- TWAK (Trust Wallet Agent Kit) ---
    twak_bin: str = Field(default="twak")
    twak_profile: str = Field(default="xorr")
    twak_password: str = Field(default="bsc-agent-2026")
    twak_password_env: str = Field(default="TWAK_PASSWORD")
    # Trust Wallet API credentials — REQUIRED for the real TWAK path (wallet
    # create/swap/register). Get them via `twak setup`. When absent, the agent
    # falls back to the local web3 self-custody keystore.
    twak_access_id: str = Field(default="")
    twak_hmac_secret: str = Field(default="")

    # --- Self-custody agent wallet ---
    # Optional explicit private key (else an encrypted keystore is generated under
    # data_store/agent_keystore.json on first run). NEVER commit a real key.
    agent_private_key: str = Field(default="")
    swap_deadline_sec: int = Field(default=120)
    swap_gas_limit: int = Field(default=320000)
    approve_gas_limit: int = Field(default=80000)

    # --- Data providers ---
    cmc_api_key: str = Field(default="")
    cmc_mcp_api_key: str = Field(default="925563e8c6a545d597202100862e4a81")
    cmc_mcp_url: str = Field(default="https://mcp.coinmarketcap.com/skill-hub/stream")
    groq_api_key: str = Field(default="")
    groq_model: str = Field(default="llama-3.3-70b-versatile")
    # Only these two models are enabled for the provided Groq org; the verifier
    # reuses the strong model under the skeptical verifier role for a contrarian check.
    groq_council_primary: str = Field(default="llama-3.3-70b-versatile")
    groq_council_verifier: str = Field(default="llama-3.3-70b-versatile")
    groq_council_fast: str = Field(default="llama-3.1-8b-instant")
    groq_council_timeout_sec: int = Field(default=8)
    fear_greed_url: str = Field(default="https://api.alternative.me/fng/?limit=2")

    # --- Risk defaults ---
    scan_interval_sec: int = Field(default=180)        # SIGNAL layer: heavy universe scan (rate-limit safe)
    sim_scan_interval_sec: int = Field(default=60)     # scan faster in simulation (visible activity)
    monitor_interval_sec: int = Field(default=15)      # RISK/EXIT layer: fast tick on cheap Binance marks
    sim_start_usdt: float = Field(default=60.0)        # paper starting cash — mirror the live wallet ($50-70)
    sim_start_bnb: float = Field(default=0.03)         # paper BNB gas reserve
    sim_council_min: float = Field(default=0.30)       # relaxed entry bar in sim so the paper book stays active (live stays disciplined)
    max_concurrent_positions: int = Field(default=5)
    base_trade_size_usd: float = Field(default=2.0)
    slippage_bps_spot: int = Field(default=150)
    slippage_bps_news: int = Field(default=300)
    max_drawdown_pct: float = Field(default=20.0)
    kill_drawdown_pct: float = Field(default=25.0)
    cex_deviation_bps: int = Field(default=120)
    liquidity_impact_bps: int = Field(default=150)
    quality_mode: bool = Field(default=True)
    confluence_threshold: int = Field(default=70)            # trend-strategy internal bar (quality)
    confluence_threshold_relaxed: int = Field(default=45)    # trend-strategy internal bar (non-quality)
    confluence_junk_floor: int = Field(default=30)           # candidate junk filter; lets counter-trend through
    council_min_final_confidence: float = Field(default=0.62)
    council_min_final_confidence_relaxed: float = Field(default=0.55)
    # Strategy enables. The 5 disabled below are the proven LOSERS from the logged
    # backtests (fib -1.56R, vol_squeeze -0.95R, mean_reversion -0.89R,
    # momentum_pullback -0.65R, rsi_reversion -0.52R) — pruned so the competition
    # book is only the strategies with a real edge. The arbiter still auto-suspends
    # any kept strategy that bleeds live.
    enable_strategy_momentum_pullback: bool = Field(default=False)   # -0.65R (redundant w/ donchian)
    enable_strategy_fib_golden_pocket: bool = Field(default=False)   # -1.56R (worst)
    enable_strategy_capitulation: bool = Field(default=True)
    enable_strategy_news_catalyst: bool = Field(default=True)
    enable_strategy_mean_reversion: bool = Field(default=False)      # -0.89R
    enable_strategy_trend_follow: bool = Field(default=True)
    enable_strategy_vol_squeeze: bool = Field(default=False)         # -0.95R
    enable_strategy_whale_flow: bool = Field(default=True)
    enable_strategy_donchian_breakout: bool = Field(default=True)    # +1.35R star (spot long)
    enable_strategy_donchian_perp: bool = Field(default=False)       # -> shadow: failed comm2x (dies at 2x cost)
    enable_strategy_salamander_perp: bool = Field(default=True)      # long/short perp pullbacks — best shorts (+0.55R)
    enable_strategy_supertrend_perp: bool = Field(default=False)     # marginal (+0.04R); available, off by default
    enable_strategy_rsi_reversion: bool = Field(default=False)       # -0.52R
    enable_strategy_xsect_momentum: bool = Field(default=True)

    # Researched-strategy params (TradingView popular defaults)
    supertrend_period: int = Field(default=10)
    supertrend_mult: float = Field(default=3.0)
    salamander_pullback_min: float = Field(default=3.0)
    salamander_pullback_max: float = Field(default=7.0)
    donchian_channel_bars: int = Field(default=55)          # trustdan-validated: 55-bar >> 20-bar (+5.1% vs +2.4%)
    enable_strategy_volsqueeze_perp: bool = Field(default=False)
    enable_strategy_rsi_div_perp: bool = Field(default=False)
    # Liquidation-flow strategies (moon-dev class). Need the real liq tape live;
    # backtest uses a kline proxy. Shadow-only until proven on live data.
    enable_strategy_liq_reversion_perp: bool = Field(default=True)   # ENABLED: +0.247R, 63% win, +28.7% (proxy) — best in book
    enable_strategy_liq_zscore_perp: bool = Field(default=False)     # continuation: weak (-0.05R), shadow-only
    enable_strategy_liq_relspike_perp: bool = Field(default=False)   # continuation: breakeven, shadow-only
    liq_z_threshold: float = Field(default=2.5)            # cascade z-score gate (moon-dev: 2.5sigma)
    # Adaptive-percentile family (self-calibrating threshold vs fixed z — robust)
    adaptive_percentile: float = Field(default=95.0)       # fire when move/liq >= Nth pctile of its own recent regime
    adaptive_lookback: int = Field(default=100)            # bars for the percentile window
    # ENABLED — survived the 4-way robustness gauntlet (OOS/Sens/comm2x/multi):
    enable_strategy_adaptive_percentile_reversion_perp: bool = Field(default=True)   # OOS +0.203, rank #2
    enable_strategy_cascade_filter_perp: bool = Field(default=True)                  # OOS +0.230, rank #1
    enable_strategy_volume_confirmed_reversion_perp: bool = Field(default=True)      # OOS +0.142
    enable_strategy_adaptive_percentile_momentum_perp: bool = Field(default=False)   # failed OOS -> shadow
    enable_strategy_burst_scalper_perp: bool = Field(default=False)                  # weak survivor -> shadow
    # moon-dev momentum set (exact mechanics) — gauntlet-gated, mostly shadow
    enable_strategy_cascade_consec_perp: bool = Field(default=False)        # momentum -> failed OOS, shadow
    enable_strategy_zscore_advol_perp: bool = Field(default=False)          # momentum -> failed OOS, shadow
    enable_strategy_volume_momentum_perp: bool = Field(default=False)       # momentum -> failed OOS, shadow
    enable_strategy_dominant_burst_perp: bool = Field(default=True)         # ENABLED: survived gauntlet (OOS +0.177, 5/5, fades 5x bursts)
    enable_strategy_adaptive_p99_momentum_perp: bool = Field(default=False) # momentum -> failed OOS, shadow
    consec_bars: int = Field(default=4)                    # cascade = N consecutive same-dir bars
    burst_imbalance_ratio: float = Field(default=5.0)      # dominant-side burst: one side 5x the other
    # 5 NEW performance liquidation-reversion ideas — each hit 500%+/<25%DD in the
    # compounding backtest AND survived the known/unknown OOS split. ENABLED. (Live
    # they trade at the conservative perp caps; the 500% is the edge at 5x demo sizing.)
    enable_strategy_liq_double_extreme_perp: bool = Field(default=True)     # +708% / OOS +221%
    enable_strategy_liq_mtf_reversion_perp: bool = Field(default=True)      # +1014% / OOS +456%
    enable_strategy_liq_range_extreme_perp: bool = Field(default=True)      # +529% / OOS +149%
    enable_strategy_liq_vwap_reversion_perp: bool = Field(default=True)     # +575% / OOS +87%
    enable_strategy_liq_rsi_stack_perp: bool = Field(default=True)          # +1360% / OOS +340%
    liq_big_z_threshold: float = Field(default=3.0)        # "big liq" gate for the reversal-fade
    liq_relspike_threshold: float = Field(default=3.0)     # relative-spike gate (vs rolling regime)
    # MACD + liq strategies ("momentum is the right way to trade liquidations")
    enable_strategy_macd_regime_perp: bool = Field(default=False)
    enable_strategy_liq_macd_momentum_perp: bool = Field(default=False)
    enable_strategy_macd_liq_reversal_perp: bool = Field(default=False)
    # Liq + trend-break ideas — ENABLED winners (reversion beats continuation, again):
    enable_strategy_liq_support_reversion_perp: bool = Field(default=True)   # +0.215R, 68% win, 5.1% DD
    enable_strategy_liq_climax_reversion_perp: bool = Field(default=True)    # +0.145R, 63% win, 3.4% DD
    enable_strategy_liq_squeeze_break_perp: bool = Field(default=False)      # DISABLED: failed OOS (-0.027) — overfit caught by the gauntlet
    # (the 5 continuation ideas were negative -> left disabled; 2 marginal -> shadow below)
    # Registered-but-disabled strategies that run as SHADOW (paper) live; the arbiter
    # auto-promotes any whose shadow expectancy proves out (>=8 trades, >0.25R).
    shadow_test_strategies: str = Field(default="supertrend_perp,volsqueeze_perp,rsi_div_perp,liq_zscore_perp,liq_relspike_perp,macd_regime_perp,liq_macd_momentum_perp,macd_liq_reversal_perp,liq_divergence_fade_perp,liq_failed_breakdown_perp,donchian_perp,burst_scalper_perp,adaptive_percentile_momentum_perp,cascade_consec_perp,zscore_advol_perp,volume_momentum_perp,adaptive_p99_momentum_perp")
    # geektrade vol-squeeze (BB inside KC + volume), exact published params
    volsq_len: int = Field(default=20)
    volsq_bb_mult: float = Field(default=2.0)
    volsq_kc_mult: float = Field(default=1.5)
    volsq_vol_spike: float = Field(default=1.8)
    volsq_sl_atr: float = Field(default=2.0)
    volsq_tp_atr: float = Field(default=3.5)
    rsi_div_lookback: int = Field(default=20)

    # --- Perpetual futures (BSC perps via TWAK -> Aster/Hyperliquid) ---
    enable_perps: bool = Field(default=True)                 # master switch for the perp book
    # (enable_strategy_donchian_perp lives with the other strategy enables below)
    # Liquid majors traded as perps (long & short). Intersected at runtime with the
    # 149 eligible list AND the tokens that have Binance klines for signals.
    # Only majors that are BOTH on the 149 eligible list AND returned by CMC quotes
    # (BTC=BTCB/SOL are NOT on the list -> no quote -> can't size/value -> excluded).
    # Keeps perp PnL unambiguously in-scope and every position priceable.
    perp_symbols: str = Field(default="ETH,BNB,XRP,DOGE,ADA,AVAX,LINK")
    perp_leverage: float = Field(default=3.0)               # conservative default; liquidation ~33% away
    perp_max_leverage: float = Field(default=5.0)           # hard clamp (DQ-cap discipline)
    perp_usd_is_margin: bool = Field(default=True)          # CLI --usd = margin (verify w/ 1 dust perp on comp machine)
    perp_margin_pct_per_trade: float = Field(default=0.12)  # max margin per perp = 12% of equity
    perp_total_margin_pct: float = Field(default=0.30)      # max TOTAL perp margin = 30% of equity
    perp_min_margin_usd: float = Field(default=5.0)         # venue min order floor
    perp_funding_rate_8h: float = Field(default=0.0001)     # funding carry modeled per 8h on notional (sim/backtest realism)
    enable_funding_fade: bool = Field(default=True)         # CMC funding-rate skills bias perp signals (fade the crowded side)

    # --- Drawdown disqualification gate (competition rule ~30%) ---
    dq_drawdown_pct: float = Field(default=30.0)            # blow past this = DISQUALIFIED
    flatten_drawdown_pct: float = Field(default=22.0)       # soft tier: flatten + pause, recoverable
    risk_pause_min: float = Field(default=120.0)            # how long to pause new entries after a soft flatten
    perp_liq_guard_pct: float = Field(default=6.0)          # force-close a perp if mark within this % of liquidation
    data_max_staleness_sec: int = Field(default=180)        # circuit breaker: don't open trades on a frozen price feed

    # --- Mode ---
    start_mode: str = Field(default="simulation")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
