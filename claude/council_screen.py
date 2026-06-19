"""Groq council screen — the cheap "second opinion" half of the council discussion.

Turns the top confluence candidates into provisional long Signals and runs the existing
3-model Groq council (brain.council.score_council) over them. The council's per-symbol score
+ red flags are handed to Claude as a second opinion. Groq is fast + free (API key), so it's
the cheap pre-screen; Claude makes the final, binding call seeing BOTH confluence + Groq.

This is the "agent council of Groq and Claude discussing between themselves": Groq screens
and flags, Claude verifies against the confluence and decides. Fail-open: any error → empty
read, and Claude just decides without it.
"""
from datetime import datetime, timezone
from typing import List, Optional

from config import settings
from core.types import Signal, MarketContext
from data.tokens import resolve


def _archetype_strategy(side: str) -> str:
    return "donchian_breakout" if side == "breakout" else "liq_support_reversion_perp"


def _candidate_signal(c: dict) -> Optional[Signal]:
    sym = str(c.get("symbol", "")).upper()
    if not sym:
        return None
    t = resolve(sym)
    panel = c.get("confluence") or {}
    side = panel.get("side", "reversion")
    conf = float(panel.get("score") or c.get("reversion_score") or 0.5)
    firing = ", ".join(panel.get("firing", [])) or "score-based"
    return Signal(
        symbol=sym, contract=(t.contract if t else ""), side="buy",
        confidence=max(0.05, min(0.97, conf)),
        stop_loss_pct=3.5, take_profit_pct=5.0, max_hold_min=480,
        rationale=f"{side} confluence {panel.get('agree', 0)}/{panel.get('total', 0)} ({firing})",
        strategy_name=_archetype_strategy(side), direction="long", venue="spot", leverage=1.0,
    )


def _ctx(regime: str) -> MarketContext:
    return MarketContext(
        timestamp=datetime.now(timezone.utc), fear_greed_value=50, fear_greed_label="Neutral",
        btc_dominance=55.0, total_market_cap_usd=0.0, total_market_cap_change_24h=0.0,
        bnb_price_usd=0.0, regime=regime or "CHOP", confluence=50.0,
    )


async def groq_screen(candidates: List[dict], regime: str) -> dict:
    """Return {SYMBOL: {score, red_flags, reasoning}} from the Groq council, or {} on failure."""
    if not bool(getattr(settings, "enable_groq_screen", True)):
        return {}
    sigs = [s for s in (_candidate_signal(c) for c in candidates) if s]
    if not sigs:
        return {}
    try:
        from brain.council import score_council
        decisions = await score_council(sigs, _ctx(regime), min_conf=0.0)
    except Exception as e:
        print(f"[council_screen] groq screen skipped: {e}")
        return {}
    out = {}
    for d in decisions:
        vote = d.votes[0] if d.votes else {}
        out[d.symbol.upper()] = {
            "score": round(float(d.final_confidence), 2),
            "red_flags": vote.get("redFlags", []),
            "reasoning": str(vote.get("reasoning", ""))[:160],
        }
    return out
