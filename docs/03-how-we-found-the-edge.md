# 03 — How We Found the Edge

> The most important document. The "best strategy" was not chosen — it was the
> survivor of a process. This explains the process, so you can trust the result
> and repeat it for any new idea.

## The core problem
The competition is judged on **live PnL** over one week, with a **~30% drawdown =
disqualification** gate. Two hard truths shaped everything:

1. **You can't win a PnL contest with a long-only spot agent in a down/choppy
   week** — with no ability to short, the best case is "sit in cash ≈ breakeven."
   So the agent *had* to be able to short → **perpetual futures** (long AND short).
2. **In-sample backtests lie.** Every flashy strategy looks great on the data it
   was tuned on. The only number that matters is performance on data the strategy
   has **never seen**, at **realistic cost**, on **other assets**.

## The funnel (how a strategy earns a place)
Every idea — ours, from TradingView repos, from the moon-dev liquidation videos —
goes through the same funnel. Most die. That's the point.

```
  idea  ->  implement natively  ->  in-sample backtest (real data + costs)
        ->  if positive: 4-way ROBUSTNESS GAUNTLET
        ->  survives all 4?  -> ENABLE (risk real capital)
        ->  marginal/needs-live-data? -> SHADOW (paper-trade live, auto-promote)
        ->  fails? -> DISABLED (kept in code, never risked)
```

### Step 1 — Implement natively, backtest with *realistic* costs
We never trust a screenshot's numbers. We re-implement the strategy's logic in our
own engine and backtest it on **real Binance klines** with **honest costs** (0.25%
pool fee + slippage for spot; taker fee + funding carry for perps). The very first
finding was that the original cost model was unrealistic and made *every* trade a
guaranteed loss — fixing it (to ~0.7% round-trip for a liquid swap) changed every
conclusion. **Cost realism is the foundation.**

### Step 2 — The 4-way Robustness Gauntlet (the real test)
A strategy only gets to risk capital if it survives **all four** of these on 1h
BTC/ETH/SOL data (see [04-robustness.md](04-robustness.md)):

| Test | Question it answers |
|---|---|
| **OOS** | Does it work on the *unseen* second half of history, or only where it was tuned? |
| **Sens** | Does a ±15%/±30% nudge to its main parameter break it? (overfit detector) |
| **comm2x** | Is its edge bigger than *double* the trading cost, or is it cost-fragile? |
| **Multi** | Does it generalize to *other assets* (ETH, SOL), or is it BTC-curve-fit? |

This gauntlet has **caught our own mistakes**: a strategy we'd enabled on a great
in-sample number (`liq_squeeze_break`, +0.147R) turned out to **fail OOS** and was
disabled; another (`donchian_perp`) was robust to parameters but **lost money at 2×
commission** and was demoted. Without the gauntlet, both would have bled live.

## What the process actually found
After running **dozens** of strategies through this funnel across many rounds, one
result appeared over and over — **8 independent times**:

> **On the liquid majors, you FADE liquidation flushes. You do not chase them.**

When forced liquidations spike (a cascade of stop-outs), price overshoots and then
**snaps back**. Buying that forced-selling dip (and shorting forced-buying spikes)
is a genuine, repeatable, out-of-sample-robust edge. The mirror-image **momentum /
continuation** strategies — "follow the cascade," "trade the breakout," "ride the
trend" — were tested *just as rigorously* and **failed out-of-sample every single
time** on our 1h majors.

The cleanest proof of this is a single A/B: the **`adaptive_percentile`** strategy
in two flavours that differ *only* in direction —
- **reversion** (fade): OOS expectancy **+0.203R** → survives, **enabled**.
- **momentum** (follow): OOS expectancy **−0.017R** → fails, **shadow**.

Same trigger, opposite action, opposite result. That is the edge in one line.

## Why momentum still gets a shadow, not a delete
The moon-dev videos show momentum liquidation strategies working — but on **5-minute
data with the real liquidation tape over 18 months**, which we can't fully
reproduce (our backtest uses 1h klines and a price/volume *proxy* for the liq tape).
So we don't claim they're worthless — we **shadow-test them live** against the real
Binance liquidation feed. If the real tape proves them out (≥8 paper trades >0.25R),
the arbiter **auto-promotes** them. Honesty + a live test beats an argument.

## The ranking that resulted (top survivors, by OOS expectancy)
1. `cascade_filter` +0.230 · 2. `adaptive_percentile_reversion` +0.203 ·
3. `salamander` +0.191 (5/5 Sens) · 4. `liq_support_reversion` +0.184 ·
5. `liq_climax_reversion` +0.170 · 6. `liq_reversion` +0.151 ·
7. `volume_confirmed_reversion` +0.142 · 8. `dominant_burst` +0.177 (a 5× burst
*fade*).

Every one is a **fade/reversion** strategy. That is not a coincidence — it's the
edge the process found.

## How to trust a new idea tomorrow
Run `python -X utf8 -m backtest.robustness --all`. If a new strategy doesn't appear
in the SURVIVORS line, it does not get enabled — no matter how good its in-sample
chart looks. That rule is the whole methodology.
