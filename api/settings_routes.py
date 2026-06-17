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
    groqModel: Optional[str] = Field(default=None, alias="groq_model")
    qualityMode: Optional[bool] = Field(default=None, alias="quality_mode")
    confluenceThreshold: Optional[int] = Field(default=None, alias="confluence_threshold")
    councilMinFinalConfidence: Optional[float] = Field(default=None, alias="council_min_final_confidence")
    groqCouncilPrimary: Optional[str] = Field(default=None, alias="groq_council_primary")
    groqCouncilVerifier: Optional[str] = Field(default=None, alias="groq_council_verifier")
    groqCouncilFast: Optional[str] = Field(default=None, alias="groq_council_fast")
    enableStrategyMomentumPullback: Optional[bool] = Field(default=None, alias="enable_strategy_momentum_pullback")
    enableStrategyFibGoldenPocket: Optional[bool] = Field(default=None, alias="enable_strategy_fib_golden_pocket")
    enableStrategyCapitulation: Optional[bool] = Field(default=None, alias="enable_strategy_capitulation")
    enableStrategyNewsCatalyst: Optional[bool] = Field(default=None, alias="enable_strategy_news_catalyst")
    enableStrategyMeanReversion: Optional[bool] = Field(default=None, alias="enable_strategy_mean_reversion")
    enableStrategyTrendFollow: Optional[bool] = Field(default=None, alias="enable_strategy_trend_follow")
    enableStrategyVolSqueeze: Optional[bool] = Field(default=None, alias="enable_strategy_vol_squeeze")
    enableStrategyWhaleFlow: Optional[bool] = Field(default=None, alias="enable_strategy_whale_flow")

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
        "groqModel": settings.groq_model,
        "qualityMode": settings.quality_mode,
        "confluenceThreshold": settings.confluence_threshold,
        "councilMinFinalConfidence": settings.council_min_final_confidence,
        "groqCouncilPrimary": settings.groq_council_primary,
        "groqCouncilVerifier": settings.groq_council_verifier,
        "groqCouncilFast": settings.groq_council_fast,
        "enableStrategyMomentumPullback": settings.enable_strategy_momentum_pullback,
        "enableStrategyFibGoldenPocket": settings.enable_strategy_fib_golden_pocket,
        "enableStrategyCapitulation": settings.enable_strategy_capitulation,
        "enableStrategyNewsCatalyst": settings.enable_strategy_news_catalyst,
        "enableStrategyMeanReversion": settings.enable_strategy_mean_reversion,
        "enableStrategyTrendFollow": settings.enable_strategy_trend_follow,
        "enableStrategyVolSqueeze": settings.enable_strategy_vol_squeeze,
        "enableStrategyWhaleFlow": settings.enable_strategy_whale_flow
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

class StrategyToggle(BaseModel):
    enabled: bool

@router.post("/settings/strategy/{name}")
def toggle_strategy(name: str, payload: StrategyToggle):
    """Allows operator to override arbiter by force-enabling/disabling a strategy."""
    attr_name = f"enable_strategy_{name}"
    if hasattr(settings, attr_name):
        setattr(settings, attr_name, payload.enabled)
        
    from strategies.arbiter import arbiter
    if payload.enabled:
        arbiter.suspended_strategies.discard(name)
        # Clear bad history to reset expectancy baseline
        from sqlmodel import Session, delete
        from persistence.db import engine as db_engine
        from persistence.models import StrategyStat
        with Session(db_engine) as session:
            session.execute(delete(StrategyStat).where(StrategyStat.strategy == name))
            session.commit()
    else:
        arbiter.suspended_strategies.add(name)
        
    return {"status": "ok", "name": name, "enabled": payload.enabled}
