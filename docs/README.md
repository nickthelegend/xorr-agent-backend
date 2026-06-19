# XORR Agent — Documentation

XORR is a **regime-adaptive autonomous trading agent** for the
**BNB Hack: AI Trading Agent Edition** (CoinMarketCap × Trust Wallet × BNB Chain,
Track 1). It trades **long-only spot on PancakeSwap** (the competition is
**spot-only** — see [10 — Spot-Only Pivot](11-spot-only.md)), reads the market from
CoinMarketCap + live Binance feeds, and is judged on **live PnL with a hard ~30%
max-drawdown disqualification gate**.

> ⚠️ **Spot-only.** Perps are disabled (`SPOT_ONLY=true`). The proven reversion
> strategies still run, but only their **long (buy-the-flush)** side executes, as 1×
> spot. No leverage, no shorting, **no liquidation risk** → the DQ gate is easy to
> hold. Much of the historical text below describes the long/short *perp* design that
> the edge was discovered with; [11 — Spot-Only Pivot](11-spot-only.md) documents the
> conversion and the honest re-validation.

This folder documents what was built, why, and — most importantly — **how the best
strategy was actually found** (it wasn't guessed; it was earned through backtests
and an out-of-sample robustness gauntlet).

## Read in this order
1. [01 — Architecture](01-architecture.md) — the system: backend, frontend, the decision pipeline, the layers.
2. [02 — Strategy Catalog](02-strategies.md) — every strategy (42), what it does, and its status (enabled / shadow / disabled).
3. [03 — How We Found the Edge](03-how-we-found-the-edge.md) ⭐ — the methodology. The single most important doc.
4. [04 — Robustness Gauntlet](04-robustness.md) — the 4-way OOS test that gates every strategy.
5. [05 — Risk & Execution](05-risk-and-execution.md) — perps, liquidation, the DQ-proof kill switch, TWAK.
6. [06 — Data & Feeds](06-data-feeds.md) — CMC skills, websocket price feed, the liquidation tape.
7. [07 — The Journey](07-the-journey.md) — chronological record of everything that was asked and built.
8. [08 — Performance Strategies](08-performance-strategies.md) — the 5 strategies that hit 500%+ / <25% DD + their OOS check.
9. [09 — Verification](09-verification.md) ✅ — proof the sim, all layers, and the live path are tested and working.
10. [10 — trader.dev](10-trader-dev.md) — mining a 36k-strategy database; it independently confirms reversion>momentum. We ported its 3 best oscillators and gauntleted them (1 enabled, 2 shadow).
11. [11 — Spot-Only Pivot](11-spot-only.md) ⭐ — the competition is spot-only. What that costs, what survives (11/12 reversion strategies stay OOS-positive long-only), and exactly what changed.
12. [12 — Claude Decision Brain](12-claude-brain.md) ⭐ — replaces the weak Groq council: a 4-hourly watchlist agent scores ~70 coins and Claude (via your subscription CLI) picks what to play + the strategy. No API key.

Also at the repo root: [`COMPETITION_RUNBOOK.md`](../COMPETITION_RUNBOOK.md) (go-live steps) and [`ROBUSTNESS.md`](../ROBUSTNESS.md) (the ranked survival table).

## The one-paragraph summary
The agent runs a **signal layer** (12+ strategies across spot + perps), a
**decision layer** (multi-factor confluence + an LLM council), and a **risk layer**
(per-trade + portfolio caps, a two-tier kill switch that keeps drawdown under the
disqualification gate). The dominant, **8×-confirmed and out-of-sample-validated
edge** is **fading liquidation flushes on the liquid majors** — when forced
selling/buying spikes, price tends to snap back. Momentum/continuation strategies
were tested just as hard and **consistently failed out-of-sample**, so they're
disabled or shadow-tested, never risked blindly.

## Current book — SPOT-ONLY, long-only (auto-changes as the arbiter promotes/suspends)
Every strategy below now executes as a **1× spot long** (the `*_perp` names are historical —
they're run long-only on spot; see [11 — Spot-Only Pivot](11-spot-only.md)).
- **Enabled (19):** the spot momentum/breakout book (donchian_breakout ⭐, trend_follow, whale_flow, capitulation, news_catalyst, xsect_momentum) + the buy-the-flush reversion book (liq_reversion, liq_support_reversion, liq_climax_reversion, adaptive_percentile_reversion, cascade_filter, volume_confirmed_reversion, dominant_burst, the 5 perf reversion strategies, and aroon_mr) — **11/12 of the reversion family stay OOS-positive long-only**.
- **Disabled for spot:** `salamander_perp` — its edge was *shorting* rallies; long-only it loses (−2%/−6% OOS), so it's off.
- **Shadow-tested:** paper-traded; the arbiter auto-promotes any that prove out (≥8 trades, >0.25R). Includes `tsi_mr` / `uo_mr` (trader.dev oscillators that passed OOS but failed 2× cost).
