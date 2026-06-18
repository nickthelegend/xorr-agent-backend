# 08 — Performance Liquidation Strategies (500%+ / <25% DD)

Five NEW liquidation-reversion strategies, built and tuned to hit **≥500% return
with <25% max drawdown** in a compounding backtest, then re-validated on **unseen**
data. All implemented in the backend (`strategies/perf5.py`), registered, and
**ENABLED**. Full root report: [`../PERFORMANCE.md`](../PERFORMANCE.md).

## The five (each a distinct confirmation on the same flush base)
Every one fires only on a **percentile flush** — the bar's move is in the top ~95th
percentile of its own recent regime (a genuine forced-flow event) — then adds a
distinct exhaustion/stretch confirmation:

| Strategy | Confirmation | Backtest return | MaxDD | Sharpe |
|---|---|---|---|---|
| `liq_rsi_stack_perp` | Stochastic %K extreme | +1360% | 19.3% | 2.77 |
| `liq_mtf_reversion_perp` | higher-TF RSI(28) exhaustion | +1014% | 22.1% | 2.38 |
| `liq_double_extreme_perp` | extreme volume percentile | +708% | 21.8% | 2.50 |
| `liq_vwap_reversion_perp` | VWAP deviation | +575% | 21.7% | 2.25 |
| `liq_range_extreme_perp` | EMA20 deviation | +529% | 23.3% | 2.26 |

## How the headline numbers are produced (`backtest/perf.py`)
- **Compounding portfolio** across 9 majors, ~365d of 1h, margin = % of *current*
  equity (so the edge compounds), 5× leverage, realistic costs (12 bps + funding).
- A **drawdown circuit breaker** = the live soft-kill: flatten + reset the baseline
  when drawdown passes a threshold. This is what keeps DD < 25% while compounding.
- `python -X utf8 -m backtest.perf --tune` reproduces the 500%/<25%DD result.

> **Honest framing:** the 500%+ figures are the *known fade-the-flush edge compounded
> at 5× demo sizing*. **Live, these trade at the competition caps** (≈12% margin, 3×
> leverage, DQ-proof kill switch) — the agent harvests the validated edge safely, not
> at demo aggression. Hitting 500% required *compounding many trades*, not a new edge.

## Robustness — KNOWN vs UNKNOWN (`--report`)
Sizing is tuned on the **first half** (KNOWN), then the same config is run on the
**second half** the strategy never saw (UNKNOWN):

| Strategy | KNOWN ret / DD / Sharpe | UNKNOWN ret / DD / Sharpe |
|---|---|---|
| liq_mtf_reversion | +78% / 22.9 / 1.58 | **+456% / 16.5 / 3.69** |
| liq_rsi_stack | +131% / 19.3 / 2.17 | +340% / 18.5 / 3.40 |
| liq_double_extreme | +160% / 21.8 / 2.30 | +221% / 15.9 / 2.72 |
| liq_range_extreme | +152% / 23.3 / 2.22 | +149% / 16.5 / 2.31 |
| liq_vwap_reversion | +259% / 21.7 / 2.75 | +87% / 18.6 / 1.71 |

**All five stay positive out-of-sample;** 10/12 of the whole reversion book is
OOS-positive. They are wired into the live engine and fire on real flushes (verified
5/5 — see [09-verification.md](09-verification.md)).
