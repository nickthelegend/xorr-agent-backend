# XORR Strategy Robustness Gauntlet

Every perp strategy is run through a **4-way out-of-sample gauntlet** on 1h data
with realistic costs. A strategy is only *enabled* (risked with real capital) if it
**survives all four**. Reproduce: `python -X utf8 -m backtest.robustness [--all]`.

## The 4 tests
| Test | What it does | Pass |
|---|---|---|
| **OOS** | reference on first half of BTC, score on the **unseen second half** | OOS expectancy > 0 |
| **Sens** | perturb the strategy's primary parameter by ±15% and ±30% | ≥ 3 of 5 stay positive |
| **comm2x** | **double** the round-trip commission | expectancy still > 0 |
| **Multi** | same params on **ETH and SOL** | ETH or SOL positive |

Ranked by **OOS expectancy** (R per trade on data the strategy never saw). Returns
are small in absolute % because the harness uses fixed sizing + no compounding —
**expectancy and survival are the signal**, not headline return.

## Results (≈200d 1h, BTC/ETH/SOL)

| Rank | Strategy | OOS exp | Sens | comm2x | ETH/SOL | Survives |
|---|---|---|---|---|---|---|
| 1 | cascade_filter_perp ⭐NEW | +0.230 | 3/5 | +0.120 | +/+ | ✅ |
| 2 | adaptive_percentile_reversion_perp ⭐NEW | +0.203 | 3/5 | +0.090 | +/+ | ✅ |
| 3 | salamander_perp | +0.191 | 5/5 | +0.190 | +/+ | ✅ |
| 4 | liq_support_reversion_perp | +0.184 | 5/5 | +0.122 | +/+ | ✅ |
| 5 | liq_climax_reversion_perp | +0.170 | 5/5 | +0.150 | +/~ | ✅ |
| 6 | liq_reversion_perp | +0.151 | 5/5 | +0.080 | +/+ | ✅ |
| 7 | volume_confirmed_reversion_perp ⭐NEW | +0.142 | 3/5 | +0.099 | +/+ | ✅ |
| 8 | burst_scalper_perp ⭐NEW | +0.088 | 4/5 | +0.012 | +/+ | ✅(weak) |
| 9 | liq_divergence_fade_perp | +0.007 | 5/5 | +0.023 | +/+ | ✅(marginal) |
| — | supertrend_perp | +0.092 | 0/5 | −0.075 | −/− | ❌ Sens+cost |
| — | donchian_perp | +0.013 | 5/5 | **−0.040** | +/− | ❌ **dies at 2× cost** |
| — | liq_zscore_perp (momentum) | +0.003 | 3/5 | −0.030 | +/− | ❌ cost |
| — | adaptive_percentile_momentum_perp | **−0.017** | 3/5 | −0.017 | +/~ | ❌ **OOS** |
| — | liq_squeeze_break_perp | **−0.027** | 5/5 | +0.043 | −/+ | ❌ **OOS (overfit)** |

## Decisions the gauntlet forced
- **Enabled** the 3 strong new survivors: `cascade_filter_perp`, `adaptive_percentile_reversion_perp`, `volume_confirmed_reversion_perp`.
- **Disabled** `liq_squeeze_break_perp` — I had enabled it last round on an in-sample +0.147R, but it **fails OOS (−0.027)**. Classic overfit, caught.
- **Demoted** `donchian_perp` to shadow — robust to parameters (5/5) but **loses money at 2× commission**, so its edge is thinner than realistic costs.
- Confirmed the standing reversion book (liq_reversion / support / climax + salamander) is genuinely robust (mostly 5/5 Sens, positive OOS + comm2x + cross-asset).

## The repeated headline
Across every test: **reversion/fade survives, momentum/continuation does not.**
`adaptive_percentile_reversion` survives (+0.203 OOS); its `momentum` twin fails
(−0.017 OOS). On the majors, you **fade** liquidation flushes.
