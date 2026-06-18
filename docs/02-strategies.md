# 02 — Strategy Catalog

42 strategies are **saved** (committed code + registered). Status is decided by
backtest + the robustness gauntlet, not by hand:
- **ENABLED (14)** — survived the gauntlet; risk real capital.
- **SHADOW (17)** — paper-traded live; the arbiter auto-promotes any that prove
  out (≥8 paper trades, >0.25R). Includes momentum strategies that need the real
  5m liquidation tape to express their edge.
- **DISABLED (11)** — failed backtest/gauntlet (overfit, momentum, cost-fragile).

> "Saved" ≠ "enabled." Everything is in the code and on GitHub; enabling is what
> requires beating the gauntlet.

## ENABLED
### Spot book (PancakeSwap, long-only)
| Strategy | Idea |
|---|---|
| `donchian_breakout` ⭐ | 20-bar channel breakout, the proven spot star (+1.35R) |
| `trend_follow` | multi-timeframe EMA trend + pullback entry |
| `whale_flow` | ride positive on-chain whale netflow |
| `capitulation` | rebound after an >8%/1h flush on volume climax |
| `news_catalyst` | buy listing/news pumps (regime-bypass) |
| `xsect_momentum` | cross-sectional relative strength |

### Perp book (Aster/BSC, long + short) — all reversion survivors
| Strategy | Idea | OOS exp |
|---|---|---|
| `cascade_filter` | adaptive-percentile flush + volume, fade | +0.230 |
| `adaptive_percentile_reversion` | fade a move > Nth pctile of its own regime | +0.203 |
| `salamander` | pullback: buy dips in uptrends / short rallies in downtrends | +0.191 |
| `liq_support_reversion` | fade a flush that hits support/resistance | +0.184 |
| `dominant_burst` | fade a 5× one-sided liquidation burst (snap-back) | +0.177 |
| `liq_climax_reversion` | fade a volume-climax flush at extremes | +0.170 |
| `liq_reversion` | fade liquidation imbalance (the original A4) | +0.151 |
| `volume_confirmed_reversion` | fade a flush with a 2× volume confirm | +0.142 |

## SHADOW (paper-traded live; auto-promote if proven)
Momentum / continuation and marginal ideas that the 1h proxy gauntlet didn't pass
but which may work on the **real liquidation tape**:
`supertrend`, `volsqueeze`, `rsi_div`, `liq_zscore`, `liq_relspike`, `macd_regime`,
`liq_macd_momentum`, `macd_liq_reversal`, `liq_divergence_fade`,
`liq_failed_breakdown`, `donchian_perp` (cost-fragile), `burst_scalper`,
`adaptive_percentile_momentum`, `cascade_consec`, `zscore_advol`,
`volume_momentum`, `adaptive_p99_momentum`.

## DISABLED (failed; kept in code)
`fib_golden_pocket` (−1.56R), `vol_squeeze` (−0.95R), `mean_reversion` (−0.89R),
`momentum_pullback` (−0.65R), `rsi_reversion` (−0.52R), `liq_squeeze_break`
(failed OOS — overfit), `liq_trendbreak`, `liq_ribbon_flip`, `liq_structure_break`,
`liq_donchian_accel`, `liq_wick_rejection` (all negative continuation ideas).

## The pattern
Read the ENABLED perp book top-to-bottom: **every one is a fade/reversion.** Read
the SHADOW/DISABLED list: **almost every one is momentum/continuation.** That split
is the entire finding — see [03-how-we-found-the-edge.md](03-how-we-found-the-edge.md).
