# 07 — The Journey

A chronological record of what was asked and what was built. Useful for
understanding *why* the system is shaped the way it is.

## 1. "Make it ready to trade and win"
Audited the inherited codebase. Found the agent was **spot long-only** — which
can't win a PnL contest in a down week. Confirmed the hackathon allows **BSC perps**
and TWAK executes them via Aster. Decision: add **regime-adaptive long/short
perps** — the single biggest lever. Built: perp math (liquidation/uPnL/equity),
perp execution in the TWAK executor (sim + live CLI), a long/short breakout
strategy, perp-aware equity everywhere, the **DQ-proof two-tier kill switch**, and
validated long/short beats long-only on a down window. Fixed a real spot-swap CLI
bug along the way.

## 2. "Set TWAK creds, run signals fast, find best strategies, be production-ready"
- Couldn't fabricate TWAK API secrets (they're issued to your Trust Wallet account)
  — set up the wiring + generated the self-custody keystore + **gitignored the
  private key** (it wasn't protected before).
- Explained that a literal 5s full-scan would get rate-limited; built a **two-tier
  cadence** (fast 15s risk tick + slower signal scan).
- Implemented **salamander** (pullback) and **supertrend**; salamander became a
  top performer. Declined to ship **piranha** (needs OI/orderbook depth klines
  lack) rather than ship it unvalidated.
- Wrote **live-execution tests** (mocked twak) and answered honestly: no real
  on-chain trade has run (no funds/creds here), but the live *path* is tested.
- Listed the honest long-run gaps: no websockets, no funding accounting, no
  on-chain battle-testing, overfitting risk.

## 3. "Fix the gaps + add CMC funding skills + websockets + shadow-test"
- Closed the gaps: **funding-rate carry** in PnL, **perp boot-reconciliation**
  (fixed a latent bug that would have false-closed every perp on restart), a
  **watchdog** that auto-restarts dead loops + `/health`.
- Wired the **CMC funding-rate skills** into a perp **funding-fade** bias.
- Added the **real-time Binance websocket** price feed.
- Built **shadow-testing**: disabled strategies paper-trade live and the arbiter
  **auto-promotes** any that prove out — and fixed that the live arbiter recorded
  no stats before, so suspend/revive/promote now actually works live.

## 4. "Add the moon-dev liquidation strategies"
Built the **Binance liquidation feed** (the forced-liq tape) + cascade detection
(real feed live, kline proxy for backtest) + the user's top 3 (imbalance reversion,
cascade z-score, relative spike). Honest read of the screenshots: the Sharpe-16 /
+137k% numbers are in-sample compounding artifacts. Our independent proxy backtest
agreed **A4 reversion is the real standout**; continuation isn't. Surfaced the liq
feed on the frontend.

## 5. "Add MACD + more liquidation ideas"
Implemented MACD dual-mode regime, MACD continuation gate, and the "momentum is the
right way to trade liquidations" headline. Backtested honestly: **none beat the
book**; the momentum headline **went negative**. All shadow-tested. Then built **10
liq + trend-break ideas** — and again **every reversion idea won, every
continuation idea lost**; enabled 3 reversion winners.

## 6. "Build robustness tests + adaptive percentile + rank everything"
The turning point. Built the **4-way robustness gauntlet** (OOS / Sens / comm2x /
Multi) — see [04-robustness.md](04-robustness.md). Added the **adaptive-percentile**
family (self-calibrating thresholds), which produced the **two best strategies by
OOS**. The gauntlet **caught two of our own enabled strategies** (one overfit, one
cost-fragile) and demoted them. The book is now **robustness-gated**: nothing gets
enabled on in-sample numbers.

## 7. "Test the screenshot momentum strategies + document everything"
Implemented the exact mechanics (consecutive bars, adaptive-vol z-score, volume
momentum, 5× imbalance burst, p99) and ran them through the gauntlet. **One
survived** — the dominant-side burst scalper (which *fades* the burst), enabled.
The four pure-momentum ones failed OOS and were shadowed (they may work on the real
5m liq tape). Wrote this `docs/` set.

## The throughline
Every round asked for "more strategies." The lasting value wasn't any single
strategy — it was the **process** that reliably separates real edges from in-sample
mirages, and the **one edge that kept surviving it**: *fade liquidation flushes on
the majors.* Everything in the enabled book is there because it earned it.
