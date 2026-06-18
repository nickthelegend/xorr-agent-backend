# 05 — Risk & Execution

The competition ranks on return **but disqualifies you at ~30% drawdown**. So the
risk layer's first job is: *never let drawdown approach 30%* — "most profit without
blowing up."

## Perp math — `core/perp_math.py`
Pure, tested primitives: isolated-margin **liquidation price**, directional
**unrealized PnL**, and a position's **equity contribution** (spot = units×price;
perp = margin + directional uPnL, floored at 0). At the default **3× leverage** the
liquidation price is ~33% away from entry, while strategy stops exit at ~3% — so
liquidation is a *backstop*, never an exit.

## Sizing & caps — `risk/sizing.py`
- **Spot:** base size scaled by Fear&Greed, drawdown, council confidence, ensemble
  agreement.
- **Perp margin:** `≤ 12% of equity per trade` and `≤ 30% of equity total`, also
  capped by available USDT. Below the venue minimum → skip.
- **Liq-scaled sizing:** bigger cascades raise signal confidence → bigger margin
  (within the caps).

## The two-tier kill switch — `risk/kill_switch.py`
Sized so a single liquidation can never breach the DQ gate:
| Tier | Trigger | Action |
|---|---|---|
| **Soft de-risk** (recoverable) | drawdown ≥ 22% | flatten all + pause new entries 2h; the **daily qualifier keeps running** so the min-1-trade/day rule is still met |
| **Hard halt** | drawdown ≥ 27% (or equity < $1.10) | flatten + halt; better to lock −27% than be DQ'd at −30% |
Plus a **drawdown ladder** that scales size down from ~10% drawdown and stops new
risk by ~18%, a **liquidation guard** (force-close a perp if mark gets within 6% of
liquidation), and a **stale-feed circuit breaker** (no new entries on a frozen
price feed).

## Execution — `core/twak_executor.py`
- **Sim:** DB-backed paper ledger — real reference price, fee, funding carry,
  margin, direction-aware PnL. The default mode; everything is testable here.
- **Live:** the `twak` CLI (verified syntax from the audited `trading-agent`):
  - spot: `twak swap <amt> <FROM> <TO> --chain bsc --slippage <pct>`
  - perp: `twak perps open <SYM> --side long|short --usd <margin> --leverage <L> --chain bsc`, `perps close`, `perps mark`
  - register: `twak compete register`
  - An **eligibility guardrail** rejects any live swap whose non-USDT leg isn't on
    the 149 whitelist. Perps require TWAK creds (no web3 fallback); without them the
    agent gracefully trades spot only.
- **Boot reconciliation:** on live start, perp positions are reconciled against
  `twak perps positions` (closed/liquidated-externally positions are cleaned up);
  spot against on-chain balances. Sim is authoritative for sim.

## Tested
`tests/test_live_execution.py` mocks the `twak` subprocess and asserts the exact
CLI command + JSON parsing for spot swap, perp open/close/mark, register, and the
eligibility guard — so the live path is verified without moving real funds. The
first *real* trade happens on the funded competition machine per
[`../COMPETITION_RUNBOOK.md`](../COMPETITION_RUNBOOK.md).
