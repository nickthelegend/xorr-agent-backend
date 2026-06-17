import httpx
import json
from typing import List
from config import settings
from core.types import Signal, ScoredSignal, MarketContext
from brain.prompts import SYSTEM_PROMPT

async def score_signals(signals: List[Signal], market_ctx: MarketContext) -> List[ScoredSignal]:
    """
    Calls Groq Llama to score and rank incoming signals.
    Falls back to deterministic ranking if Groq is unavailable/disabled.
    """
    if not signals:
        return []

    # Fail open if no Groq API Key is configured
    if not settings.groq_api_key:
        print("[BRAIN] Groq API Key not set. Failing open to deterministic ranking.")
        return _fail_open(signals)

    # Format the input signals and context for the LLM
    signals_data = []
    for s in signals:
        signals_data.append({
            "symbol": s.symbol,
            "strategy": s.strategy_name,
            "confidence": s.confidence,
            "rationale": s.rationale,
            "stopLossPct": s.stop_loss_pct,
            "takeProfitPct": s.take_profit_pct
        })

    market_data = {
        "regime": market_ctx.regime,
        "fearGreedValue": market_ctx.fear_greed_value,
        "fearGreedLabel": market_ctx.fear_greed_label,
        "btcDominance": market_ctx.btc_dominance,
        "bnbPriceUsd": market_ctx.bnb_price_usd
    }

    user_message = {
        "signals": signals_data,
        "marketContext": market_data
    }

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": settings.groq_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_message)}
        ],
        "temperature": 0.1,
        # Force JSON response if supported by model
        "response_format": {"type": "json_object"}
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code == 200:
                result = response.json()
                content = result["choices"][0]["message"]["content"]
                
                # Parse JSON
                parsed = json.loads(content)
                # Some models wrap the array inside a dictionary (e.g. {"signals": [...]})
                if isinstance(parsed, dict):
                    # Find any list in the dict keys
                    for k, v in parsed.items():
                        if isinstance(v, list):
                            scored_list = v
                            break
                    else:
                        scored_list = []
                else:
                    scored_list = parsed
                    
                # Map back to ScoredSignal objects
                scored_map = {item["symbol"].upper(): item for item in scored_list if "symbol" in item}
                
                final_scored = []
                for s in signals:
                    item = scored_map.get(s.symbol.upper())
                    if item:
                        score = float(item.get("score", s.confidence * 100))
                        reasoning = item.get("reasoning", s.rationale)
                    else:
                        # If a specific signal was omitted by LLM, score it by default confidence
                        score = s.confidence * 100
                        reasoning = s.rationale
                        
                    final_scored.append(ScoredSignal(
                        signal=s,
                        score=score,
                        reasoning=reasoning
                    ))
                
                # Sort by score descending
                final_scored.sort(key=lambda x: x.score, reverse=True)
                return final_scored
            else:
                print(f"[BRAIN WARNING] Groq returned status {response.status_code}. Failing open.")
    except Exception as e:
        print(f"[BRAIN WARNING] Groq request failed or timed out: {e}. Failing open.")

    # Fall back
    return _fail_open(signals)

def _fail_open(signals: List[Signal]) -> List[ScoredSignal]:
    """Deterministic fallback sorting by signal confidence."""
    scored = []
    for s in signals:
        # Score is confidence mapped to 0-100 scale (e.g. 0.8 -> 80.0)
        score = round(s.confidence * 100.0, 1)
        scored.append(ScoredSignal(
            signal=s,
            score=score,
            reasoning=f"Deterministic fallback: scored {score} based on strategy confidence ({s.confidence})."
        ))
    # Sort by score descending
    scored.sort(key=lambda x: x.score, reverse=True)
    return scored
