# 09 — Verification: Is It Actually Working?

Everything below is *tested*, not asserted. Reproduce with the commands shown.

## Simulation — WORKING ✅
A `simulation`-mode run exercises every layer against live market data:
- **Universe:** 68 tradable tokens (the liquid subset of the 149 eligible) for the
  spot book + 7 eligible majors (ETH/BNB/XRP/DOGE/ADA/AVAX/LINK) for the perp book.
- **A real scan cycle** (no mocks, live CMC + Binance) completes through: build
  context → kill-switch check → daily qualifier → filters → strategies → council →
  risk sizing → sim execution. The daily **qualifier** trade executes (an ETH
  round-trip on the paper ledger) so the ≥1-trade/day rule is always met.
- Not every scan trades — by design. Signals fire on flushes/breakouts; a quiet
  market produces "scan complete, no signal," which is correct.

## Signal / Decision / Risk / Trade layers — ALL WORKING ✅
Verified end-to-end by feeding a real flush (a −6% bar on 8× volume) through the
**actual** `run_pipeline_cycle` in sim:
- **Signal:** all 5 new strategies (+ existing reversion) fired LONG (fade the
  flush); the shadow momentum strategies fired SHORT as paper.
- **Combiner:** merged the reversion longs into ONE high-conviction position.
- **Decision (council) + Arbiter:** scored and admitted the trade.
- **Risk:** sized the perp margin under the caps (≈12% equity), set a direction-aware
  stop + liquidation price.
- **Trade:** opened the perp on the sim ledger (cash deducted; Position created with
  entry/stop/liquidation).
- **Risk/exit (monitor):** on the next tick it managed exits — the paper shorts hit
  their stops and closed (no capital impact), the real long was managed against its
  TP/trailing.
- **Shadow system:** 8 paper positions opened with **zero** real-cash impact and
  recorded `StrategyStat` rows so the arbiter can auto-promote any that prove out.

## Live trading — IMPLEMENTED + TESTED, not yet exercised on-chain ⚠️
- **Implemented:** the `TwakExecutor` live path shells out to the real `twak` CLI:
  - spot: `twak swap <amt> <FROM> <TO> --chain bsc --slippage <pct>`
  - perps: `twak perps open/close/mark … --chain bsc`
  - registration: `twak compete register`
  - eligibility guardrail + web3 keystore fallback for spot.
- **Tested:** `tests/test_live_execution.py` — 9 tests that **mock the twak
  subprocess** and assert the exact CLI command construction + JSON parsing for spot
  swap, perp open/close/mark, register, and the eligibility guard. They pass.
- **Not yet run on-chain:** no real trade has executed, because this machine has no
  TWAK credentials or funded wallet. The first *real* trade happens on the funded
  competition machine — see [`../COMPETITION_RUNBOOK.md`](../COMPETITION_RUNBOOK.md).

## Test suite — 52 PASSING ✅
`python -m pytest tests/ -q` → 52 passed. Covers perp math, sim open/close
(long/short), the monitor's direction-aware exits, the two-tier kill switch, perp
margin caps, the live mocked-twak path, funding-fade, the shadow flow (paper close →
stat → arbiter promotion with zero cash impact), and scheduler health.

## Reproduce
```
python -m pytest tests/ -q                          # 52 tests
python -X utf8 -m backtest.robustness --all          # 4-way OOS gauntlet
python -X utf8 -m backtest.perf --tune               # the 500%/<25%DD goal
python -X utf8 -m backtest.perf --report             # known/unknown OOS (ret/DD/Sharpe)
python run.py                                        # boot the agent (simulation by default)
```
