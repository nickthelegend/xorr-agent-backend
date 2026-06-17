import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass
from groq import AsyncGroq
from sqlmodel import Session

from core.types import Signal, MarketContext
from config import settings
from brain.prompts import SYSTEM_PROMPT, ROLE_ADDENDUM_PRIMARY, ROLE_ADDENDUM_VERIFIER, ROLE_ADDENDUM_FAST
from persistence.db import engine as db_engine
from persistence.models import LLMVote

logger = logging.getLogger("xorr.brain.council")

@dataclass
class CouncilDecision:
    symbol: str
    action: str  # "enter" | "skip"
    council_score: float
    consensus: float
    final_confidence: float
    votes: List[Dict[str, Any]]
    decision_id: str

async def call_model(
    client: AsyncGroq,
    model_id: str,
    role_name: str,
    role_addendum: str,
    prompt_content: str,
    timeout: float,
    max_tokens: int
) -> dict:
    t0 = time.time()
    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n" + role_addendum},
            {"role": "user", "content": prompt_content}
        ]
        completion = await client.chat.completions.create(
            model=model_id,
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
            temperature=0.1,
            timeout=timeout
        )
        latency = int((time.time() - t0) * 1000)
        content = completion.choices[0].message.content
        data = json.loads(content)
        
        if "scores" not in data:
            raise ValueError("Response JSON missing 'scores' field")
            
        return {
            "success": True,
            "model": model_id,
            "role": role_name,
            "data": data["scores"],
            "latency": latency
        }
    except Exception as e:
        logger.warning(f"Groq model {model_id} ({role_name}) failed: {e}")
        return {
            "success": False,
            "model": model_id,
            "role": role_name,
            "error": str(e),
            "latency": int((time.time() - t0) * 1000)
        }

async def score_council(signals: List[Signal], ctx: MarketContext) -> List[CouncilDecision]:
    """
    Evaluates list of signals using the 3-model Groq Council.
    Redistributes weights proportionally on model failure, applies consensus penalty,
    and returns a list of decisions. Saves individual votes to the database.
    """
    if not signals:
        return []

    # If Groq API Key is not set (or a placeholder), use deterministic fallback
    groq_key = (settings.groq_api_key or "").strip()
    if not groq_key or groq_key.lower().startswith("your_"):
        logger.warning("[BRAIN] Groq API key not set, using deterministic fallback")
        return [deterministic_fallback(s, ctx) for s in signals]

    # Initialize client
    client = AsyncGroq(api_key=settings.groq_api_key)
    
    # 1. Format the list of signals and the market context
    prompt_content = json.dumps({
        "signals": [
            {
                "symbol": s.symbol,
                "strategy_name": s.strategy_name,
                "confidence": s.confidence,
                "rationale": s.rationale,
                "stop_loss_pct": s.stop_loss_pct,
                "take_profit_pct": s.take_profit_pct
            } for s in signals
        ],
        "context": {
            "timestamp": ctx.timestamp.isoformat() if isinstance(ctx.timestamp, datetime) else str(ctx.timestamp),
            "fear_greed_value": ctx.fear_greed_value,
            "fear_greed_label": ctx.fear_greed_label,
            "btc_dominance": ctx.btc_dominance,
            "total_market_cap_usd": ctx.total_market_cap_usd,
            "total_market_cap_change_24h": ctx.total_market_cap_change_24h,
            "regime": ctx.regime,
            "macro": ctx.macro,
            "btc_correlation": ctx.btc_correlation
        }
    }, indent=2)

    # 2. Configure 3-model council details
    models_config = [
        {
            "id": settings.groq_council_primary,
            "weight": 0.45,
            "role": "primary",
            "addendum": ROLE_ADDENDUM_PRIMARY,
            "max_tokens": 400
        },
        {
            "id": settings.groq_council_verifier,
            "weight": 0.35,
            "role": "verifier",
            "addendum": ROLE_ADDENDUM_VERIFIER,
            "max_tokens": 400
        },
        {
            "id": settings.groq_council_fast,
            "weight": 0.20,
            "role": "fast",
            "addendum": ROLE_ADDENDUM_FAST,
            "max_tokens": 120
        }
    ]

    timeout = float(settings.groq_council_timeout_sec)
    
    # Run the models concurrently
    tasks = [
        call_model(
            client,
            m["id"],
            m["role"],
            m["addendum"],
            prompt_content,
            timeout,
            m["max_tokens"]
        ) for m in models_config
    ]
    results = await asyncio.gather(*tasks)

    # 3. Filter succeeding models
    active_results = []
    failed_models = []
    
    for i, res in enumerate(results):
        config = models_config[i]
        if res["success"]:
            active_results.append((config, res))
        else:
            failed_models.append(config["id"])
            logger.error(f"[BRAIN] Model {config['id']} excluded this cycle: {res.get('error')}")

    # If all models failed, fall back to deterministic scoring
    if not active_results:
        logger.error("[BRAIN] All council models failed. Falling back to deterministic confluence score.")
        return [deterministic_fallback(s, ctx) for s in signals]

    # Redistribute weights proportionally
    total_active_weight = sum(cfg["weight"] for cfg, _ in active_results)
    
    decisions = []
    
    for signal in signals:
        # Create a unique decision ID for this signal
        decision_id = str(uuid.uuid4())
        
        votes = []
        scores = []
        
        for cfg, res in active_results:
            model_id = cfg["id"]
            role = cfg["role"]
            weight = cfg["weight"] / total_active_weight  # Normalized weight
            
            # Find the score for this specific symbol
            symbol_score = 0.5  # Default middle ground
            reasoning = "Not scored by model"
            red_flags = []
            
            for score_item in res["data"]:
                if score_item.get("symbol", "").upper() == signal.symbol.upper():
                    symbol_score = float(score_item.get("score", 0.5))
                    reasoning = score_item.get("reasoning", "")
                    red_flags = score_item.get("red_flags", [])
                    break
                    
            scores.append(symbol_score)
            
            # Record this vote
            vote_dict = {
                "model": model_id,
                "role": role,
                "score": symbol_score,
                "reasoning": reasoning,
                "redFlags": red_flags,
                "latencyMs": res["latency"],
                "normalized_weight": weight
            }
            votes.append(vote_dict)
            
            # Persist to LLMVote table
            try:
                with Session(db_engine) as session:
                    db_vote = LLMVote(
                        decision_id=decision_id,
                        model=f"{model_id} ({role})",
                        score=symbol_score,
                        reasoning=reasoning,
                        red_flags_json=json.dumps(red_flags),
                        latency_ms=res["latency"]
                    )
                    session.add(db_vote)
                    session.commit()
            except Exception as e:
                logger.warning(f"Failed to persist LLMVote: {e}")

        # Compute weighted council score
        council_score = sum(v["score"] * v["normalized_weight"] for v in votes)
        
        # Compute consensus (stddev of scores)
        if len(scores) <= 1:
            consensus = 0.0
        else:
            mean_score = sum(scores) / len(scores)
            variance = sum((x - mean_score) ** 2 for x in scores) / len(scores)
            consensus = variance ** 0.5

        # Compute consensus penalty
        consensus_penalty = min(0.3, 2.0 * consensus)
        final_confidence = council_score * (1.0 - consensus_penalty)

        # Decide Action
        min_conf = settings.council_min_final_confidence
        action = "enter" if final_confidence >= min_conf else "skip"

        decisions.append(CouncilDecision(
            symbol=signal.symbol,
            action=action,
            council_score=council_score,
            consensus=consensus,
            final_confidence=final_confidence,
            votes=[{
                "model": v["model"],
                "score": v["score"],
                "reasoning": v["reasoning"],
                "redFlags": v["redFlags"],
                "latencyMs": v["latencyMs"]
            } for v in votes],
            decision_id=decision_id
        ))

    return decisions

def deterministic_fallback(signal: Signal, ctx: MarketContext) -> CouncilDecision:
    """Computes a fallback score based on confluence_score/100."""
    decision_id = str(uuid.uuid4())
    # Blend market confluence with the strategy's own conviction (mirrors the
    # backtest council model) so counter-trend signals aren't judged on momentum alone.
    conf_component = (ctx.confluence / 100.0) if ctx.confluence else 0.5
    score = 0.5 * conf_component + 0.5 * (signal.confidence or 0.5)
    
    # Save a dummy LLMVote to represent the fallback
    try:
        with Session(db_engine) as session:
            db_vote = LLMVote(
                decision_id=decision_id,
                model="Deterministic Fallback",
                score=score,
                reasoning="Fallback due to Groq council unavailability",
                red_flags_json=json.dumps([]),
                latency_ms=0
            )
            session.add(db_vote)
            session.commit()
    except Exception as e:
        logger.warning(f"Failed to persist LLMVote fallback: {e}")

    min_conf = settings.council_min_final_confidence
    action = "enter" if score >= min_conf else "skip"
    
    return CouncilDecision(
        symbol=signal.symbol,
        action=action,
        council_score=score,
        consensus=0.0,
        final_confidence=score,
        votes=[{
            "model": "Deterministic Fallback",
            "score": score,
            "reasoning": "Fallback due to Groq council unavailability",
            "redFlags": [],
            "latencyMs": 0
        }],
        decision_id=decision_id
    )
