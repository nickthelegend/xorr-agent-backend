# 11 — The Spot-Only Pivot

> *"bad news… we can't trade in perps for the hackathon, only spot — change to spot only."*

Late in development the competition was clarified to **spot trading only — no perpetuals.**
This is a real constraint, not a config toggle, because the agent's entire *edge* had been
built around perps. This doc is the honest account of what that costs, what survives, and
exactly what changed.

---

## 1. What spot-only takes away (be honest)

The perp book gave the agent two things spot cannot:

1. **Shorting** — the ability to profit in a *falling* week. The single most-repeated
   finding in this whole project was that the SHORT side of the reversion book carried the
   edge in down/choppy tape. Spot can't short, so that half is simply gone.
2. **Leverage** — the amplifier behind the headline numbers (the 500%/<25%DD
   [performance book](08-performance-strategies.md) needed 5× compounding). At 1× those
   numbers collapse to their unleveraged size.

So the return *ceiling* drops to **single-digit % over a competition week**, and the agent
**sits in cash (USDT) during sustained downtrends** instead of shorting them.

## 2. What survives — and it's more than you'd think

The reversion edge is **"buy the oversold liquidation flush, sell into the bounce."** Its
**LONG side is a genuine spot strategy** — you buy a major after a forced-selling washout
and exit into the snap-back. That doesn't need leverage or shorting. And spot has **zero
liquidation risk** (you own the token), which makes the **30% drawdown DQ gate far easier
to respect** — the entire leverage/liquidation blow-up failure mode is gone.

So the pivot is *not* a rewrite, and **no perp code is removed** — it's a venue switch:
the proven `*_perp` reversion strategies keep running as **signal sources**; we just take
their **long side only** and execute it as a **1× spot swap**. One strategy that depended on
shorting — `salamander_perp` (sell rallies in downtrends) — is **excluded from spot** (it
stays enabled for perps; see below).

### Switching venue per run
The perp engine is fully intact and selectable at launch — nothing was deleted:

```bash
python run.py            # venue from .env (SPOT_ONLY / ENABLE_PERPS)
python run.py --spot     # force SPOT-ONLY this run (long-only spot, perps off)
python run.py --perps    # force SPOT + PERPS this run (long/short, leverage)
```

The backtest mirrors it: `python -X utf8 -m backtest.perf --spot [--all]` reports the
long-only 1× spot book; without `--spot` it reports the leveraged perp book.

## 3. Honest re-validation (long-only, 1×)

The compounding backtest harness was extended with a `long_only` + `leverage=1` mode — at
those settings it *is* a spot backtest. Run on the 9 majors, ~365d 1h, sizing tuned on the
KNOWN first half and tested on the UNSEEN second half (`python -X utf8 -m backtest.perf --spot`):

```
strategy                       cfg      | KNOWN ret% DD%  Shrp win% | UNKNOWN ret% DD%  Shrp win% OOS
liq_mtf_reversion_perp         45%/dd12 |     +8   8.1  1.04  59   |     +30   4.6  4.02  63   YES
cascade_filter_perp            85%/dd12 |    +26   7.9  2.40  59   |     +28  12.1  2.28  56   YES
liq_rsi_stack_perp             85%/dd12 |    +19  12.7  1.65  64   |     +23   8.7  2.68  61   YES
liq_support_reversion_perp     85%/dd12 |     +0   8.0  0.09  52   |     +23   3.4  3.67  66   YES
adaptive_percentile_reversion  80%/dd12 |    +37   8.8  3.02  59   |     +19  12.4  1.58  56   YES
liq_range_extreme_perp         45%/dd12 |    +23   5.9  2.52  62   |     +15   5.9  2.51  62   YES
liq_vwap_reversion_perp        85%/dd12 |    +21   6.6  2.09  57   |     +15   6.2  1.67  56   YES
liq_double_extreme_perp        45%/dd12 |    +14   7.8  1.81  62   |     +13  12.5  1.69  58   YES
liq_reversion_perp             85%/dd12 |     +9  12.0  1.11  57   |      +7  14.1  0.73  52   YES
dominant_burst_perp            45%/dd12 |     +3   2.6  0.96  65   |      +7   4.1  1.56  59   YES
liq_climax_reversion_perp      30%/dd12 |     -2   3.9 -0.52  52   |      +6   2.8  1.65  62   YES
salamander_perp                80%/dd12 |     -2  12.6  0.04  51   |      -6  12.2 -0.43  46   no
                                                         OOS-positive on the unseen half: 11/12
```

**11 of 12 stay OOS-positive long-only**, most with win rates 56–66% and drawdowns well
under 15% (vs the 30% gate). `salamander_perp` is the lone failure — negative on *both*
halves with a 46% win rate — because its edge was shorting rallies; long-only it just buys
into downtrends. So it's **excluded from the spot book** (via `spot_excluded_strategies`)
while **staying enabled for perps**. Everything else stays enabled.

> These are ~1-year compounding figures at tuned sizing. Over a single competition *week*
> they scale down to low single-digit % on average — reversion is bursty, so a week with
> real flush-and-bounce setups does better and a quiet week does ~nothing. The point of this
> table isn't the headline %, it's that the **edge survives the loss of shorts and leverage,
> out-of-sample, with drawdowns nowhere near the DQ cliff.**

## 4. Exactly what changed in the code

| Area | Change |
|---|---|
| `run.py` | `--spot` / `--perps` flags select the venue per run (set env before the app loads). **No perp code removed.** |
| `config.py` | `spot_only: bool = True` (master switch); `enable_perps` default → `False`; `spot_excluded_strategies="salamander_perp"` (skipped in spot, kept for perps) |
| `engine/pipeline.py` | spot-only branch runs the `*_perp` reversion strategies on the majors (minus the spot-excluded ones), keeps **LONG only**, routes to spot. `enforce_spot_only()` chokepoint drops any short and forces 1× spot |
| `core/twak_executor.py` | `open_perp()` **fails closed** when `spot_only` — a perp can never open, even if a signal slips through |
| `risk` / qualifier | unchanged — the daily qualifier was already a spot USDT→ETH→USDT round-trip; the drawdown ladder + kill switch still apply |
| `core/readiness.py` | reports `tradingVenue: "spot"`, `spotOnly: true`; TWAK demoted to *optional* (special prize only — not needed to trade) |
| `backtest/perf.py` | `--spot` mode (long-only, 1×) for honest re-validation |
| `.env` / `.env.example` | `SPOT_ONLY=true`, `ENABLE_PERPS=false` |
| frontend | wallet readiness shows a **SPOT-ONLY MODE** badge instead of a perpetually-off "PERPS LIVE" |

## 5. Net effect

- **Execution is simpler and safer:** spot via the local web3 keystore (PancakeSwap) — **no
  TWAK creds required to trade**, no leverage, no liquidation. The DQ gate is easy to hold.
- **The edge is intact on the long side:** buy-the-flush reversion on the majors, 11
  OOS-positive strategies, plus the spot momentum/breakout book (`donchian_breakout` ⭐ et al.).
- **The realistic expectation is lower and we say so:** modest single-digit weekly return,
  capital-preserving in down weeks. We trade for *return within the gate*, not raw upside.
