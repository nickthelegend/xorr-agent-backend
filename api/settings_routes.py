from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Optional
from api.deps import get_session, settings

router = APIRouter()

class SettingsUpdate(BaseModel):
    scanIntervalSec: Optional[int] = Field(default=None, alias="scan_interval_sec")
    maxConcurrentPositions: Optional[int] = Field(default=None, alias="max_concurrent_positions")
    baseTradeSizeUsd: Optional[float] = Field(default=None, alias="base_trade_size_usd")
    slippageBpsSpot: Optional[int] = Field(default=None, alias="slippage_bps_spot")
    slippageBpsNews: Optional[int] = Field(default=None, alias="slippage_bps_news")
    maxDrawdownPct: Optional[float] = Field(default=None, alias="max_drawdown_pct")
    killDrawdownPct: Optional[float] = Field(default=None, alias="kill_drawdown_pct")
    cexDeviationBps: Optional[int] = Field(default=None, alias="cex_deviation_bps")
    liquidityImpactBps: Optional[int] = Field(default=None, alias="liquidity_impact_bps")
    enableVedicFilter: Optional[bool] = Field(default=None, alias="enable_vedic_filter")
    enableNewsCatalyst: Optional[bool] = Field(default=None, alias="enable_news_catalyst")
    groqModel: Optional[str] = Field(default=None, alias="groq_model")

    class Config:
        populate_by_name = True

def get_settings_payload():
    return {
        "scanIntervalSec": settings.scan_interval_sec,
        "maxConcurrentPositions": settings.max_concurrent_positions,
        "baseTradeSizeUsd": settings.base_trade_size_usd,
        "slippageBpsSpot": settings.slippage_bps_spot,
        "slippageBpsNews": settings.slippage_bps_news,
        "maxDrawdownPct": settings.max_drawdown_pct,
        "killDrawdownPct": settings.kill_drawdown_pct,
        "cexDeviationBps": settings.cex_deviation_bps,
        "liquidityImpactBps": settings.liquidity_impact_bps,
        "enableVedicFilter": settings.enable_vedic_filter,
        "enableNewsCatalyst": settings.enable_news_catalyst,
        "groqModel": settings.groq_model
    }

@router.get("/settings")
def get_settings():
    return get_settings_payload()

@router.post("/settings")
def update_settings(payload: SettingsUpdate):
    update_dict = payload.model_dump(exclude_unset=True)
    for key, value in update_dict.items():
        if hasattr(settings, key):
            setattr(settings, key, value)
    return get_settings_payload()
