# 04 — Robustness Gauntlet

`backtest/robustness.py` — the gate every strategy must pass before it risks
capital. Run: `python -X utf8 -m backtest.robustness [--all]`. The ranked table is
saved at [`../ROBUSTNESS.md`](../ROBUSTNESS.md).

## Why this exists
In-sample returns are worthless as evidence — anything can be curve-fit. The honest
questions are: does it work on **unseen** data, survive **parameter** changes,
survive **real costs**, and generalize to **other assets**? A strategy that passes
all four is far more likely to be real.

## The 4 tests (per strategy, on ~200d 1h BTC/ETH/SOL)
| Test | Mechanic | Pass condition |
|---|---|---|
| **OOS** | reference on 1st-half BTC, score on the unseen 2nd half | OOS expectancy > 0 |
| **Sens** | re-run with primary param ×{0.70, 0.85, 1.15, 1.30} | ≥ 3 of 5 stay positive |
| **comm2x** | double the round-trip commission | expectancy still > 0 |
| **Multi** | same params on ETH and SOL | ETH or SOL positive |

**SURVIVES** = OOS > 0 **and** Sens ≥ 3/5 **and** comm2x > 0 **and** (ETH or SOL > 0).
Ranked by **OOS expectancy** — performance on data it never saw.

## Honest notes on the numbers
- Returns look small (single-digit %) because the harness uses **fixed sizing and
  no compounding** — by design, so a few lucky compounding trades can't inflate the
  result. **Expectancy (R per trade) and survival are the signal**, not headline %.
- We can't reproduce the moon-dev "500% / 32,462%" figures: those are BTC **5m**
  over **18 months** with aggressive compounding and the **real liquidation tape**.
  Our gauntlet is 1h, fixed-size, with a price/volume **proxy** for the liq tape.
  Different setup, deliberately conservative.

## What it caught (its value, concretely)
- **`liq_squeeze_break`** — enabled on an in-sample +0.147R, then **failed OOS
  (−0.027)**. Disabled. A textbook overfit that would have bled live.
- **`donchian_perp`** — robust to parameters (5/5 Sens) but **negative at 2×
  commission**. Its edge is thinner than realistic cost. Demoted to shadow.
- **`adaptive_percentile_momentum`** — failed OOS while its **reversion twin
  survived**, isolating direction as the deciding variable.

## Adding the gauntlet to your workflow
Every new idea: implement → in-sample backtest → if positive, run the gauntlet →
enable only if it's in the SURVIVORS line. No exceptions, no "but the chart looks
amazing." That discipline is why the enabled book is trustworthy.
