# XORR Agent Backend

Autonomous, self-custody trading agent for **BNB Smart Chain**, built for **BNB Hack Track 1**.
Powered by CoinMarketCap data, a multi-model Groq "brain council", and the
**Trust Wallet Agent Kit (TWAK)** for local signing + execution on PancakeSwap.

## What it does

- Scans the competition's eligible BEP-20 universe, runs 8 strategies through a
  filter stack (regime, CEX-sanity, liquidity, volume, whale-netflow, confluence),
  ranks survivors with an LLM council, sizes with risk guardrails, and executes
  swaps via TWAK.
- Two modes:
  - **Simulation** (default) — paper trading against real market prices. No keys,
    no wallet, no chain writes. Correct, persistent PnL (DB-backed ledger).
  - **Live** — real PancakeSwap swaps signed locally by TWAK. Requires the `twak`
    CLI, a funded agent wallet, and on-chain competition registration.

## Prerequisites

- Python 3.10+ (tested on 3.13)
- For live mode only: Node.js + TWAK CLI (`npm i -g @trustwallet/cli`), a funded BSC wallet
- Optional: CoinMarketCap Pro key + Groq key (the agent runs without them using
  free Binance data + a deterministic brain; with them you get richer quotes and
  the full LLM council)

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in keys (optional for simulation)
python scripts/build_universe.py   # build tokens.eligible.json (149 eligible, ~68 tradable)
uvicorn main:app --port 8000
```

The frontend (`../xorr-agent-frontend`) talks to this API at `http://localhost:8000`.

## Token universe (hybrid)

`tokens.eligible.json` holds all **149 eligible** competition symbols. Each entry is
flagged `tradable` only when it has BOTH a verified BSC contract (PancakeSwap token
lists) AND a Binance USDT spot pair (for price + backtest klines). The agent only
*trades* the tradable subset; the full 149 is the on-chain eligibility whitelist and
is enforced as a hard guardrail on live swaps. Rebuild any time with
`python scripts/build_universe.py`.

## Backtesting

Walk-forward backtest over the tradable subset using real Binance klines:

```bash
python -X utf8 -m backtest.cli run --window 21d --tokens all --quality-mode off
python -X utf8 -m backtest.cli list
```

Reports include total and per-strategy expectancy (R), win rate, profit factor,
max drawdown, and Sharpe, plus top/bottom symbols. Results are stored in SQLite and
exposed via `/api/backtest/*`. (Use `-X utf8` on Windows for non-ASCII token symbols.)

## Competition registration (live)

Operator-triggered, never automatic:

- API: `POST /api/engine/register`  (status: `GET /api/engine/registration`)
- or CLI: `twak compete register`

In simulation mode registration is a no-op stub. Registration contract:
`0x212c61b9b72c95d95bf29cf032f5e5635629aed5`.

## Guardrails

- **Eligibility whitelist** — live swaps into any non-eligible token are rejected.
- **Drawdown ladder** — position sizes scale down as drawdown grows; parks at >20%.
- **Kill switch** — halts and liquidates if equity < $1.10 or drawdown > 25%.
- **CEX sanity** — ignores CMC price glitches via Binance/Bybit cross-check.
- **Liquidity gate** — TWAK price-impact quote before entry.
- **Cooldowns** — per-token retrade blacklist after exits.
- **Slippage caps** — spot vs news swap slippage bounds.
- **Daily qualifier** — guarantees the ≥1 trade/day requirement (round-trips an
  eligible liquid token).

## Layout

- `api/` — FastAPI routers (overview, trades, brain, wallet, settings, engine, stream, backtest, learning, mcp)
- `core/` — TWAK executor, sim ledger, wallet, RPC, boot reconciler
- `data/` — CMC quotes (+ Binance fallback), klines, CEX oracle, fear/greed, news, tokens, CMC MCP
- `filters/` — regime, cex-sanity, liquidity, cooldown, volume, whale-netflow, confluence
- `strategies/` — 8 strategies + arbiter + registry
- `risk/` — sizing, drawdown ladder, kill switch, qualifier
- `brain/` — Groq council + decision log
- `engine/` — pipeline, monitor, scheduler, modes
- `backtest/` — engine, runner (walk-forward), data loader, reporter, CLI
- `persistence/` — SQLModel models, repo, DB (SQLite + WAL)
