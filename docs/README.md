# XORR Agent — Documentation

XORR is a **regime-adaptive long/short autonomous trading agent** for the
**BNB Hack: AI Trading Agent Edition** (CoinMarketCap × Trust Wallet × BNB Chain,
Track 1). It trades **spot on PancakeSwap** and **perpetual futures on Aster/BSC**
via the Trust Wallet Agent Kit (TWAK), reads the market from CoinMarketCap + live
Binance feeds, and is judged on **live PnL with a hard ~30% max-drawdown
disqualification gate**.

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

## Current book (auto-changes as the arbiter promotes/suspends)
- **Enabled (14):** the spot book (donchian_breakout ⭐, trend_follow, whale_flow, capitulation, news_catalyst, xsect_momentum) + the robust perp book (salamander, liq_reversion, liq_support_reversion, liq_climax_reversion, adaptive_percentile_reversion, cascade_filter, volume_confirmed_reversion, dominant_burst).
- **Shadow-tested (17):** paper-traded live; the arbiter auto-promotes any that prove out (≥8 trades, >0.25R).
- **Disabled (11):** failed the gauntlet (overfit, momentum, or cost-fragile).
