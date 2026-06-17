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
    groq_api_key: str = Field(default="")
    groq_model: str = Field(default="llama-3.3-70b-versatile")
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
    enable_vedic_filter: bool = Field(default=True)
    enable_news_catalyst: bool = Field(default=True)

    # --- Mode ---
    start_mode: str = Field(default="simulation")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
