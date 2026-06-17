import os
import json
from datetime import datetime, timezone
from pathlib import Path
from core.types import DecisionLog

DECISIONS_FILE = Path("data_store") / "decisions.jsonl"
MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

def log_decision(decision: DecisionLog):
    """Appends a DecisionLog entry to decisions.jsonl with size-based rotation."""
    # Ensure directory exists
    DECISIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    # Check rotation
    if DECISIONS_FILE.exists() and DECISIONS_FILE.stat().st_size >= MAX_SIZE_BYTES:
        try:
            # Backup and overwrite
            backup_file = DECISIONS_FILE.parent / "decisions.old.jsonl"
            if backup_file.exists():
                backup_file.unlink()
            DECISIONS_FILE.rename(backup_file)
            print(f"[DECISION LOG] Rotated log file to decisions.old.jsonl")
        except Exception as e:
            print(f"[DECISION LOG ERROR] Failed to rotate decisions file: {e}")
            
    # Serialize entry
    # handle datetime to string conversion
    t_str = decision.t.isoformat() if isinstance(decision.t, datetime) else str(decision.t)
    
    entry_dict = {
        "id": decision.id,
        "t": t_str,
        "symbol": decision.symbol,
        "action": decision.action,
        "strategy": decision.strategy,
        "filters_passed": decision.filters_passed,
        "filters_blocked": decision.filters_blocked,
        "brain_score": decision.brain_score,
        "reasoning": decision.reasoning,
        "market_snapshot": decision.market_snapshot
    }
    
    try:
        with open(DECISIONS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry_dict) + "\n")
    except Exception as e:
        print(f"[DECISION LOG ERROR] Failed to append decision: {e}")
