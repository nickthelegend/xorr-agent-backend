import os
import json
from fastapi import APIRouter, Query, HTTPException
from typing import List, Optional
from pathlib import Path

router = APIRouter()

DECISIONS_FILE = Path("data_store") / "decisions.jsonl"

def parse_decision_line(line: str) -> Optional[dict]:
    try:
        data = json.loads(line.strip())
        return {
            "id": data.get("id"),
            "t": data.get("t"),
            "symbol": data.get("symbol"),
            "action": data.get("action"),
            "strategy": data.get("strategy"),
            "filtersPassed": data.get("filters_passed", []),
            "filtersBlocked": data.get("filters_blocked", []),
            "brainScore": data.get("brain_score", 0.0),
            "reasoning": data.get("reasoning", ""),
            "marketSnapshot": data.get("market_snapshot", {}),
            "confluence": data.get("confluence"),
            "confluenceBreakdown": data.get("confluence_breakdown"),
            "council": data.get("council")
        }
    except Exception:
        return None

def read_last_lines(file_path: Path, limit: int) -> List[dict]:
    if not file_path.exists():
        return []
    
    decisions = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            # For simplicity, if file is small or moderate, we read all lines and slice.
            # If the file grows, standard rotation keeps it under 50 MB.
            lines = f.readlines()
            for line in reversed(lines):
                parsed = parse_decision_line(line)
                if parsed:
                    decisions.append(parsed)
                if len(decisions) >= limit:
                    break
    except Exception as e:
        print(f"[BRAIN ERROR] Failed to read decisions log: {e}")
        
    return decisions

@router.get("/brain/decisions")
def get_decisions(limit: int = Query(50, ge=1, le=500)):
    return read_last_lines(DECISIONS_FILE, limit)

@router.get("/brain/latest")
def get_latest_decision():
    decisions = read_last_lines(DECISIONS_FILE, 1)
    if not decisions:
        # Return a dummy placeholder or raise 404
        raise HTTPException(status_code=404, detail="No decisions logged yet.")
    return decisions[0]

@router.get("/council/health")
async def get_council_health():
    """Returns the rolling success rate for the Groq council models over the last 50 decisions."""
    from sqlmodel import Session, select
    from persistence.db import engine as db_engine
    from persistence.models import LLMVote
    
    with Session(db_engine) as session:
        # Get last 150 votes
        stmt = select(LLMVote).order_by(LLMVote.id.desc()).limit(150)
        votes = list(session.exec(stmt).all())
        
    unique_decisions = []
    seen = set()
    for v in votes:
        if v.decision_id not in seen:
            seen.add(v.decision_id)
            unique_decisions.append(v.decision_id)
        if len(unique_decisions) >= 50:
            break
            
    if not unique_decisions:
        return {
            "primary": 1.0,
            "verifier": 1.0,
            "fast": 1.0
        }
        
    decision_map = {d_id: set() for d_id in unique_decisions}
    for v in votes:
        if v.decision_id in decision_map:
            model_lower = v.model.lower()
            if "primary" in model_lower:
                decision_map[v.decision_id].add("primary")
            elif "verifier" in model_lower:
                decision_map[v.decision_id].add("verifier")
            elif "fast" in model_lower:
                decision_map[v.decision_id].add("fast")
                
    successes = {"primary": 0, "verifier": 0, "fast": 0}
    total_attempts = len(unique_decisions)
    
    for d_id in unique_decisions:
        roles = decision_map[d_id]
        if "primary" in roles:
            successes["primary"] += 1
        if "verifier" in roles:
            successes["verifier"] += 1
        if "fast" in roles:
            successes["fast"] += 1
            
    return {
        "primary": round(successes["primary"] / total_attempts, 2),
        "verifier": round(successes["verifier"] / total_attempts, 2),
        "fast": round(successes["fast"] / total_attempts, 2)
    }
