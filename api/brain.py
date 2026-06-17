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
            "marketSnapshot": data.get("market_snapshot", {})
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
