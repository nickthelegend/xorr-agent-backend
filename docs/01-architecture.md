# 01 — Architecture

Two repos: **`xorr-agent-backend`** (FastAPI + SQLModel/SQLite) and
**`xorr-agent-frontend`** (Next.js dashboard).

## The three layers (what the user asked for)
```
  SIGNAL layer  ->  DECISION layer  ->  RISK layer  ->  EXECUTION
  (find trades)     (take it or not)    (size + cap)    (spot / perp)
```

### Signal layer — `strategies/`
12+ active strategies run every scan. Two books:
- **Spot book** (PancakeSwap, long-only): donchian_breakout ⭐, trend_follow,
  whale_flow, capitulation, news_catalyst, xsect_momentum. Runs on the liquid
  subset of the 149 eligible BEP-20 tokens.
- **Perp book** (Aster/BSC, long + short): the reversion cluster (liq_reversion,
  liq_support/climax_reversion, adaptive_percentile_reversion, cascade_filter,
  volume_confirmed_reversion, dominant_burst) + salamander. Runs on 7 eligible
  liquid majors (ETH, BNB, XRP, DOGE, ADA, AVAX, LINK).

A `combiner` merges same-symbol same-direction signals into one higher-conviction
entry (so 5 reversion strategies firing on one flush size **up**, not into 5
positions). A `StrategyArbiter` enforces diversity, a 50% per-symbol concentration
cap, auto-suspends bleeders, and auto-promotes proven shadow strategies.

### Decision layer — `filters/` + `brain/`
- **Confluence filters:** regime gate (TREND_UP/DOWN/CHOP/RISK_OFF), CEX-sanity
  (ignore glitch prices), volume gate, whale-netflow, liquidity gate (TWAK
  price-impact quote), cooldown blacklist.
- **Funding-fade:** the CMC funding-rate skills bias perp signals — boost a signal
  that fades an extreme crowd, suppress one trading with it.
- **LLM council** (`brain/council.py`): a Groq/Llama board scores each signal
  0–1 and outputs a consensus; fail-open to deterministic rules if rate-limited.

### Risk layer — `risk/`
Drawdown ladder, per-perp + total-perp margin caps, liquidation guard, and a
**two-tier kill switch** that keeps drawdown under the ~30% disqualification gate.
See [05-risk-and-execution.md](05-risk-and-execution.md).

### Execution layer — `core/`
- **Sim mode (default):** a DB-backed paper ledger (`sim_ledger.py`) — real
  reference prices, fees, funding, margin, liquidation math.
- **Live mode:** the `TwakExecutor` shells out to the `twak` CLI for spot swaps,
  perp open/close/mark, and on-chain competition registration; falls back to a
  local web3 self-custody keystore for spot when TWAK creds are absent.

## The loop — `engine/`
- **`scheduler.py`** runs two loops + a watchdog:
  - **Scan loop** (signal layer) — heavier, rate-limit-safe interval.
  - **Monitor loop** (risk/exit layer) — fast 15s tick on real-time WS marks for
    SL/TP/trailing/liquidation management.
  - **Watchdog** — auto-restarts a dead loop; `GET /health` exposes liveness +
    feed status for an external uptime monitor.
- **`pipeline.py`** is one scan cycle: build context → kill-switch check →
  daily qualifier → filters → strategies (spot + perp + shadow) → combine →
  arbiter → council → size → execute.

## Persistence — `persistence/`
SQLite (WAL). Tables: Position (spot + perp + shadow), Trade, StrategyStat (the
rolling-R series the arbiter uses), RuntimeState, EquityPoint, EngineLog,
CooldownEntry, BacktestRun, McpSkillCache. Additive column migrations on boot.

## Backtesting — `backtest/`
- `engine.py` — the spot multi-strategy walk-forward engine.
- `perp_backtest.py` — the long/short perp harness (direction-aware, leveraged,
  funding-aware).
- `robustness.py` — the 4-way OOS gauntlet (the gate for enabling anything).
