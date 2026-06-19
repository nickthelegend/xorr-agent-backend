# 12 — The Claude Decision Brain

> *"lets take decision and trades with claude… make another folder called claude, every 4 hours a watchlist agent scans ~70 coins, scores each (volume spike, ATR volatility gap, etc.), claude picks what to play, then we use the best strategy from the live list."*

The Groq LLM council was the weak link — it gated real signals with mushy scores. This
layer replaces it with **Claude as the actual trade decision-maker**, driven by your
**Claude Pro/Max subscription** (not a metered API key).

## How it works

```
every 4h ──▶ watchlist_agent ──▶ features ──▶ claude_brain ──▶ playbook ──▶ pipeline
            (scan ~70 coins)   (score each)  (Claude picks)   (cache 4h)  (force-enter)
```

1. **`claude/watchlist_agent.py`** scans the eligible BEP-20 universe (~70 coins), pulling
   1h klines for each (bounded concurrency) and ranking by opportunity score.
2. **`claude/features.py`** computes per-coin technical features — **volume spike** (vs 20-bar
   avg), **ATR %** + **ATR expansion** (the volatility gap = breakout fuel), **4h/24h
   momentum**, **RSI**, **stretch from EMA20**, **position in the 48-bar range** — plus two
   derived archetype scores (`reversion_score`, `breakout_score`).
3. **`claude/claude_brain.py`** hands the scored watchlist + the market regime + our **enabled
   strategy menu** to Claude and gets back a structured **playbook**: which coins to play, the
   best-fit strategy for each (it matches `reversion` strategies to oversold flushes,
   `breakout`/`trend` to upside thrust), a conviction (0–1), and a reason.
4. **`claude/playbook.py`** caches the playbook (in-memory + JSON, ~4h TTL) and converts the
   picks into spot **long** signals.
5. **The pipeline** injects those signals each scan and **force-enters** them — Claude's picks
   bypass the Groq council entirely (Claude already decided).

## Subscription, not API key

Claude is invoked via the **`claude` CLI in headless mode**:

```bash
claude -p "<scored watchlist + menu>" --output-format json --model claude-opus-4-8 \
       --system-prompt "<XORR spot-only trading brain>" --disallowed-tools Bash Read … WebSearch
```

The CLI authenticates with the machine's **logged-in Claude subscription**, so each decision
costs **subscription quota, not API credits** — and there's nothing to set up beyond being
logged into Claude Code. At ~6 calls/day (every 4h) the usage is negligible. The only
requirement: the agent must run on a machine where the `claude` CLI is installed and logged in.

> **Fail-open.** If the `claude` CLI is missing or errors, `claude_brain` falls back to a
> deterministic pick straight from the watchlist scores (oversold → a reversion strategy,
> breakout → donchian_breakout, regime-gated), so the agent always produces a playbook.

## A real run

`python -m claude.watchlist_agent --decide` on a choppy, broadly-red tape produced:

```json
{
  "market_view": "Choppy, broadly red tape — most names flushed into range lows and deeply
                  oversold; favor selective mean-reversion buys, avoid chasing extended breakouts.",
  "picks": [{"symbol": "SAHARA", "strategy": "stochrsi_mr_perp", "conviction": 0.55,
             "reason": "Deepest oversold (RSI 21.8) pinned at range low; clean snap-back."}],
  "avoid": ["COMP", "KAVA", "TRX", "DEXE", "AVAX", "FF"],
  "source": "claude"
}
```

Note the discipline: **one** pick in a bad tape, a reversion strategy correctly assigned to an
oversold setup, and an `avoid` list dodging both the overbought chases (COMP RSI 70, KAVA RSI 87)
*and* the falling knives (AVAX, FF). This is exactly the judgment the Groq council lacked.

## Enabling it

Off by default (`enable_claude_brain=False`) so nothing changes until you opt in:

```bash
# .env
ENABLE_CLAUDE_BRAIN=true
# (the `claude` CLI must be installed + logged into your subscription on this machine)
```

Knobs (config.py): `claude_model` (default `claude-opus-4-8`), `watchlist_interval_hours` (4),
`watchlist_universe_size` (70), `watchlist_max_picks` (5), `claude_min_conviction` (0.55),
`claude_timeout_sec` (120).

Test it live: `python -m claude.watchlist_agent --decide`. Tests: `python -m pytest
tests/test_claude_brain.py -q` (9, network-free — the CLI is mocked).

## Honest notes

- **Claude curates *what* to play; the strategy label sets the SL/TP profile.** The watchlist
  features establish the live setup (oversold now, breaking out now), Claude vets it, and the
  pick becomes a spot-long entry. It is not a per-bar technical re-evaluation — it's a
  4-hourly human-grade judgment call over a scored board, which is the point.
- **It still respects every guardrail.** Claude's picks flow through the same sizing, per-trade
  caps, drawdown ladder, and DQ-proof kill switch. Claude decides direction/selection; the risk
  system remains non-negotiable.
- **Spot-only.** Claude is instructed it can only BUY (long) — no shorts, no leverage.
