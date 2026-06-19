"""Claude decision brain — drives the `claude` CLI in headless mode (`claude -p`),
using the user's Claude **subscription** (NOT an Anthropic API key). Given the scored
watchlist + market regime + our ENABLED strategies, Claude returns a structured
playbook: which coins to play and which enabled strategy fits each.

Why the CLI and not the `anthropic` SDK: the user has a Claude Pro/Max subscription,
not a pay-as-you-go API key. The `claude` CLI authenticates with that subscription, so
shelling out to it costs no API credits. The agent must run on a machine where `claude`
is installed and logged in (the same machine that runs the bot).

Fail-open: if the CLI is missing or errors, a deterministic fallback picks straight from
the watchlist scores, so the agent ALWAYS produces a usable playbook.
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
from typing import List, Optional

from config import settings

_SYSTEM_MD_PATH = os.path.join(os.path.dirname(__file__), "SYSTEM.md")

# --- strategy archetype classification (name substring -> (type, one-liner)) ---
_ARCH = [
    ("stochrsi_mr", "reversion", "Buy when Stochastic-RSI is oversold (mean-reversion)."),
    ("rsi_stack", "reversion", "Buy oversold flush confirmed by Stochastic %K."),
    ("vwap_reversion", "reversion", "Buy a flush stretched below VWAP."),
    ("range_extreme", "reversion", "Buy a flush stretched far below EMA20."),
    ("mtf_reversion", "reversion", "Buy oversold flush confirmed on the higher timeframe."),
    ("double_extreme", "reversion", "Buy a volume-confirmed extreme down-flush."),
    ("climax_reversion", "reversion", "Buy a volume-climax capitulation at the lows."),
    ("support_reversion", "reversion", "Buy a liquidation flush into support."),
    ("adaptive_percentile_reversion", "reversion", "Buy a self-calibrating top-percentile down move (fade)."),
    ("volume_confirmed_reversion", "reversion", "Buy an oversold flush confirmed by a volume spike."),
    ("cascade_filter", "reversion", "Buy after a filtered liquidation cascade exhausts."),
    ("dominant_burst", "reversion", "Fade a dominant 5x imbalance burst (snap-back)."),
    ("liq_reversion", "reversion", "Buy the oversold liquidation flush, sell the bounce."),
    ("capitulation", "reversion", "Buy a capitulation candle after a sharp flush."),
    ("donchian_breakout", "breakout", "Buy a Donchian channel breakout (upside momentum)."),
    ("trend_follow", "trend", "Buy a pullback to the trend in a confirmed uptrend."),
    ("xsect_momentum", "relative-strength", "Buy the strongest relative performers."),
    ("whale_flow", "flow", "Buy when whale netflow is strongly positive."),
    ("news_catalyst", "event", "Buy on a fresh listing / news catalyst."),
]


def _classify(name: str):
    n = name.lower()
    for key, typ, desc in _ARCH:
        if key in n:
            return typ, desc
    return "spot", "Long spot strategy."


def _spot_book() -> List[str]:
    """Enabled strategies that actually trade in spot (active minus spot-excluded)."""
    from strategies.registry import active_strategies
    excl = {s.strip() for s in (getattr(settings, "spot_excluded_strategies", "") or "").split(",") if s.strip()}
    return [s.name for s in active_strategies(settings) if s.name not in excl]


def _strategy_menu() -> List[dict]:
    out = []
    for n in _spot_book():
        typ, desc = _classify(n)
        out.append({"name": n, "type": typ, "desc": desc})
    return out


_SYSTEM = (
    "You are the trading brain for XORR, an autonomous SPOT-ONLY, LONG-ONLY crypto agent "
    "competing in a contest judged on total return with a HARD ~30% max-drawdown "
    "disqualification gate. Constraints you must respect: you can only BUY (go long) liquid "
    "BEP-20 majors on PancakeSwap — no shorting, no leverage, no perps. Your job: from a "
    "scored watchlist, pick the few highest-conviction LONG setups and assign each the "
    "best-fit strategy from the ENABLED menu (use the strategy 'type' to match the setup: "
    "reversion for oversold flushes near range lows, breakout/trend for upside thrust near "
    "range highs). Be selective and capital-preserving: in RISK_OFF or down-trending "
    "regimes, prefer mean-reversion (buying oversold flushes) and pick fewer names; never "
    "chase. You are running LIVE and the user wants the agent ACTIVE: when genuine oversold "
    "flushes exist (deeply oversold RSI pinned at range lows, strong reversion_score), take "
    "the 1-3 cleanest as STARTER longs with conviction scaled to quality and a TIGHT "
    "invalidation just below support — don't sit in pure cash when real reversion setups are "
    "on the board. Only return zero picks if the board is genuinely featureless. "
    "LEARN from recent_performance: favor strategies winning lately, be cautious with "
    "ones bleeding. If a setup is LIVE RIGHT NOW (already deeply oversold / tagging the band / "
    "breaking out this bar), set entry_price at or just below the current price so the trade "
    "can be taken now; only set a deeper dip level when anticipating a pullback that hasn't "
    "happened yet. It is fine to return zero picks if nothing is compelling. Do not use any "
    "tools; reason only from the data provided. Respond with ONLY a JSON object, no prose, no "
    "code fences."
)


def _load_system() -> str:
    """The system prompt = claude/SYSTEM.md (human-editable) if present, else the built-in
    fallback string. Lets the user retune the brain's philosophy without touching code."""
    try:
        with open(_SYSTEM_MD_PATH, "r", encoding="utf-8") as f:
            md = f.read().strip()
        if md:
            return md
    except Exception:
        pass
    return _SYSTEM


def _recent_performance(limit: int = 40) -> List[dict]:
    """Recent closed-trade results grouped by strategy — the learning signal fed to Claude."""
    try:
        from persistence.db import engine
        from sqlmodel import Session, select
        from persistence.models import Trade
        with Session(engine) as s:
            rows = s.exec(select(Trade).where(Trade.status.in_(("win", "loss")))
                          .order_by(Trade.id.desc()).limit(limit)).all()
        by = {}
        for t in rows:
            st = t.strategy or "?"
            d = by.setdefault(st, {"n": 0, "wins": 0, "pnl": 0.0})
            d["n"] += 1
            d["wins"] += 1 if (t.pnl_usd or 0) > 0 else 0
            d["pnl"] += float(t.pnl_usd or 0)
        return [{"strategy": k, "trades": v["n"],
                 "win_pct": round(100.0 * v["wins"] / v["n"]) if v["n"] else 0,
                 "avg_pnl_usd": round(v["pnl"] / v["n"], 3) if v["n"] else 0.0}
                for k, v in sorted(by.items(), key=lambda kv: -kv[1]["pnl"])]
    except Exception:
        return []


def _build_user_prompt(watchlist: dict, max_picks: int, council: Optional[dict] = None) -> str:
    send_top = int(getattr(settings, "watchlist_send_top", 12))
    payload = {
        "regime": watchlist.get("regime"),
        "scanned": watchlist.get("scanned"),
        "watchlist": watchlist.get("ranked", [])[:send_top],   # each carries its `confluence` panel
        "groq_council": council or {},                          # weaker second opinion + red_flags, by SYMBOL
        "enabled_strategies": _strategy_menu(),
        "recent_performance": _recent_performance(),
        "instructions": {
            "pick_count": f"0 to {max_picks}",
            "rules": [
                "LONG spot only — every pick is a BUY.",
                "strategy MUST be exactly one 'name' from enabled_strategies.",
                "conviction is 0.0-1.0 and MUST scale with confluence.",
                "Match strategy.type to the setup (reversion vs breakout/trend).",
                "Each coin has a `confluence` panel: how many independent indicator lenses "
                "(agree/total) + which ones (firing) confirm a long NOW. This is your "
                "'is it real?' check — 1 lens is noise, 3+ agreeing is a real setup. Demand it.",
                "`groq_council[SYMBOL]` is a weaker second opinion (score 0-1 + red_flags). "
                "Weight concrete red_flags; override vague ones. You make the final call.",
                "Favor strategies winning in recent_performance; be wary of ones bleeding.",
                "entry_price sets a price ALERT the bot waits for. If the setup is ALREADY live "
                "(deeply oversold / flushed this bar / tagging the band), set entry_price = the "
                "current price so it fills on the next scan. Only set a lower DIP level (a few %% "
                "below) when you are anticipating a further pullback that hasn't happened yet. "
                "For breakout/trend, entry_price is just ABOVE current price (buy the break).",
                "invalidation_price is the level that KILLS the idea — below support for "
                "reversion, a failed-breakout level for breakouts. If price hits it first, no trade.",
                "Fewer, higher-quality picks beat many marginal ones; it is fine to return none.",
            ],
            "output_schema": {
                "market_view": "one sentence on the tape",
                "picks": [{"symbol": "TICKER", "strategy": "exact_menu_name",
                           "conviction": 0.0, "entry_price": 0.0, "invalidation_price": 0.0,
                           "reason": "short"}],
                "avoid": ["TICKER", "..."],
            },
        },
    }
    return ("Pick what to play from this scored watchlist. Return ONLY the JSON object "
            "described in instructions.output_schema.\n\n" + json.dumps(payload))


def _extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def _call_claude_sync(user_prompt: str) -> Optional[dict]:
    """Invoke `claude -p` headless (subscription auth). Returns the parsed decision dict."""
    bin_ = shutil.which(getattr(settings, "claude_cli_bin", "claude")) or getattr(settings, "claude_cli_bin", "claude")
    if not shutil.which(bin_) and not bin_.endswith("claude"):
        return None
    cmd = [
        bin_, "-p", user_prompt,
        "--output-format", "json",
        "--model", getattr(settings, "claude_model", "claude-opus-4-8"),
        "--system-prompt", _load_system(),
        "--dangerously-skip-permissions",   # headless decision call must never block on a prompt
        "--disallowed-tools", "Bash", "Read", "Edit", "Write", "WebSearch", "WebFetch", "Task",
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=int(getattr(settings, "claude_timeout_sec", 120)))
    except Exception as e:
        print(f"[CLAUDE] CLI call failed: {e}")
        return None
    if p.returncode != 0:
        print(f"[CLAUDE] CLI exit {p.returncode}: {(p.stderr or '')[:200]}")
        return None
    try:
        env = json.loads(p.stdout)
    except Exception:
        return None
    if env.get("is_error"):
        print(f"[CLAUDE] error: {str(env.get('result'))[:200]}")
        return None
    return _extract_json(str(env.get("result", "")))


def _fallback_playbook(watchlist: dict, max_picks: int, min_conv: float) -> dict:
    """Deterministic pick straight from the scores when Claude is unavailable."""
    regime = watchlist.get("regime", "CHOP")
    menu = {m["name"]: m for m in _strategy_menu()}
    rev = next((n for n, m in menu.items() if m["type"] == "reversion"), None)
    brk = next((n for n, m in menu.items() if m["type"] == "breakout"), rev)
    down = regime in ("RISK_OFF", "TREND_DOWN")
    min_agree = int(getattr(settings, "confluence_min_agree", 2))
    picks = []
    for r in watchlist.get("ranked", []):
        if len(picks) >= (3 if down else max_picks):
            break
        panel = r.get("confluence") or {}
        # If we have confluence data, require real multi-lens agreement (verify it's real).
        if panel and panel.get("agree", 0) < min_agree:
            continue
        # Prefer the confluence verdict on which side this is, else the feature scores.
        if panel.get("side"):
            is_rev = panel["side"] != "breakout"
        else:
            is_rev = r["reversion_score"] >= r["breakout_score"]
        if down and not is_rev:
            continue  # in a down tape only buy flushes
        strat = rev if is_rev else brk
        conv = float(panel["score"]) if panel.get("score") else (r["reversion_score"] if is_rev else r["breakout_score"])
        if not strat or conv < min_conv:
            continue
        price = float(r.get("price", 0)) or 0.0
        if is_rev:
            entry = round(price * 0.985, 8)   # buy a ~1.5% dip into the flush
            invalid = round(price * 0.95, 8)  # bail if it breaks ~5% lower (falling knife)
        else:
            entry = round(price * 1.01, 8)    # buy a ~1% break up
            invalid = round(price * 0.985, 8) # failed breakout
        picks.append({"symbol": r["symbol"], "strategy": strat,
                      "conviction": round(conv, 2),
                      "entry_price": entry, "invalidation_price": invalid,
                      "reason": ("oversold flush" if is_rev else "upside breakout") +
                                f" (rsi {r['rsi']:.0f}, ret4h {r['ret_4h']:.1f}%, volx {r['vol_spike']:.1f})"})
    return {"market_view": f"deterministic fallback ({regime})", "picks": picks,
            "avoid": [], "source": "fallback"}


def _validate(pb: dict, watchlist: dict, max_picks: int, min_conv: float) -> dict:
    """Keep only well-formed picks on enabled strategies / scanned symbols."""
    menu = {m["name"] for m in _strategy_menu()}
    syms = {r["symbol"] for r in watchlist.get("ranked", [])}
    def _num(x):
        try:
            v = float(x)
            return v if v > 0 else None
        except Exception:
            return None

    clean = []
    for p in (pb.get("picks") or []):
        try:
            sym = str(p["symbol"]).upper()
            strat = str(p["strategy"])
            conv = float(p.get("conviction", 0))
        except Exception:
            continue
        if strat in menu and conv >= min_conv and len(clean) < max_picks:
            clean.append({"symbol": sym, "strategy": strat, "conviction": round(conv, 2),
                          "entry_price": _num(p.get("entry_price")),
                          "invalidation_price": _num(p.get("invalidation_price")),
                          "reason": str(p.get("reason", ""))[:200]})
    pb["picks"] = clean
    pb["avoid"] = [str(a).upper() for a in (pb.get("avoid") or [])][:20]
    pb.setdefault("source", "claude")
    return pb


async def decide_playbook(watchlist: dict) -> dict:
    """Full council flow: research the watchlist → VERIFY each top candidate against all the
    strats' math (confluence) → get the Groq council's second opinion → only then ask Claude
    to make the binding call. Claude is GATED behind real confluence, so on a featureless tape
    it isn't called at all (saves subscription usage). Fail-open to a confluence-aware
    deterministic pick if the Claude CLI is missing or errors.
    """
    max_picks = int(getattr(settings, "watchlist_max_picks", 5))
    min_conv = float(getattr(settings, "claude_min_conviction", 0.55))
    top_n = int(getattr(settings, "confluence_top_n", 10))
    ranked = watchlist.get("ranked", [])

    # 1. Confluence: verify the top candidates with every indicator lens (deterministic).
    try:
        from claude.confluence import attach_confluence
        await attach_confluence(ranked, top_n)
    except Exception as e:
        print(f"[CLAUDE] confluence skipped: {e}")

    # 2. Council: the Groq council's cheap second opinion on those candidates.
    council = {}
    try:
        from claude.council_screen import groq_screen
        council = await groq_screen(ranked[:top_n], watchlist.get("regime"))
    except Exception as e:
        print(f"[CLAUDE] groq screen skipped: {e}")

    # 3. Gate: if we computed confluence and NOTHING is real, skip the Claude call entirely
    #    (no usage) and publish zero picks — we only spend tokens when there's a real setup.
    if bool(getattr(settings, "claude_gate_on_confluence", True)):
        panels = [r["confluence"] for r in ranked if r.get("confluence")]
        min_agree = int(getattr(settings, "confluence_min_agree", 2))
        if panels and max((p.get("agree", 0) for p in panels), default=0) < min_agree:
            return {"market_view": f"no real confluence on the board ({watchlist.get('regime')}); "
                                   f"standing aside.", "picks": [], "avoid": [],
                    "source": "gated-no-confluence", "council": council}

    # 4. Claude makes the binding decision, seeing confluence + council + recent performance.
    prompt = _build_user_prompt(watchlist, max_picks, council)
    raw = await asyncio.to_thread(_call_claude_sync, prompt)
    if raw and isinstance(raw, dict) and "picks" in raw:
        pb = _validate(raw, watchlist, max_picks, min_conv)
        pb["council"] = council
        return pb
    pb = _fallback_playbook(watchlist, max_picks, min_conv)
    pb["council"] = council
    return pb
