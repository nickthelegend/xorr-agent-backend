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

    # --- TWAK ---
    twak_bin: str = Field(default="twak")
    twak_profile: str = Field(default="xorr")
    twak_password: str = Field(default="bsc-agent-2026")
    twak_password_env: str = Field(default="TWAK_PASSWORD")

    # --- Data providers ---
    cmc_api_key: str = Field(default="")
    cmc_mcp_api_key: str = Field(default="925563e8c6a545d597202100862e4a81")
    cmc_mcp_url: str = Field(default="https://mcp.coinmarketcap.com/skill-hub/stream")
    groq_api_key: str = Field(default="")
    groq_model: str = Field(default="llama-3.3-70b-versatile")
    groq_council_primary: str = Field(default="llama-3.3-70b-versatile")
    groq_council_verifier: str = Field(default="openai/gpt-oss-120b")
    groq_council_fast: str = Field(default="llama-3.1-8b-instant")
    groq_council_timeout_sec: int = Field(default=8)
    fear_greed_url: str = Field(default="https://api.alternative.me/fng/?limit=2")

    # --- Risk defaults ---
    scan_interval_sec: int = Field(default=180)
    max_concurrent_positions: int = Field(default=3)
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
    enable_strategy_momentum_pullback: bool = Field(default=True)
    enable_strategy_fib_golden_pocket: bool = Field(default=True)
    enable_strategy_capitulation: bool = Field(default=True)
    enable_strategy_news_catalyst: bool = Field(default=True)
    enable_strategy_mean_reversion: bool = Field(default=True)
    enable_strategy_trend_follow: bool = Field(default=True)
    enable_strategy_vol_squeeze: bool = Field(default=True)
    enable_strategy_whale_flow: bool = Field(default=True)

    # --- Mode ---
    start_mode: str = Field(default="simulation")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
