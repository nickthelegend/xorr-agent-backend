# XORR — BNB Hack Track 1 Go-Live Runbook

**Competition:** BNB Hack: AI Trading Agent Edition (CoinMarketCap × Trust Wallet × BNB Chain)
**Submit by:** June 21 · **Live trading:** June 22–28 · **Judged on:** total return, with a **~30% max-drawdown cap = disqualification**. Min **1 trade/day**, only the **149 eligible BEP-20 tokens** count, hold a non-zero in-scope balance at the start, keep a **>$1 portfolio** every hour.

This agent is a **regime-adaptive long/short** system:
- **Spot long book** (PancakeSwap via TWAK) — the proven `donchian_breakout` (+1.35R).
- **Perp long/short book** (BSC perps via TWAK → Aster) — `donchian_perp`: **longs breakouts in confirmed uptrends, SHORTS breakdowns in confirmed downtrends.** This is what lets the agent **profit in a down week** instead of sitting in cash. Backtest (real data, 80d): adding shorts took the book from **+1.58% → +5.54%** at 3x with maxDD 6.5% (well under the 30% gate).

---

## Two ways to go live (important)
The agent has **two** independent on-chain execution paths:
1. **Spot via web3 keystore — needs ONLY a funded wallet, no TWAK creds.** The agent
   signs real PancakeSwap V2 swaps locally. Fund the wallet → switch to LIVE → it
   trades the spot book on-chain. (Verified: BSC connects, real PancakeSwap quotes work.)
2. **Perps via TWAK CLI — needs TWAK creds too.** The long/short perp book (and the
   special prize) require `TWAK_ACCESS_ID`/`HMAC` from `twak setup`.

The **Wallet page → GO-LIVE READINESS** panel (and `GET /api/readiness`) shows a live
checklist of exactly what's done and what's missing for each path. Use it.

## 0. One-time machine setup (do this before June 21)

```bash
# Node + the real Trust Wallet Agent Kit CLI
npm i -g @trustwallet/cli
twak --version            # expect 0.19.x+

# Authenticate (creates HMAC creds locally; keys never leave the host)
twak setup                # paste TWAK_ACCESS_ID + TWAK_HMAC_SECRET from portal.trustwallet.com
twak wallet create        # creates the encrypted self-custody agent wallet
twak wallet address --chain bsc   # <-- THIS is your agent address; copy it
```

Backend deps:
```bash
cd xorr-agent-backend
python -m venv .venv && . .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`.env` (copy from `.env.example`) — the lines that matter for LIVE + perps:
```
TWAK_PASSWORD=<your wallet password>
TWAK_ACCESS_ID=<from twak setup>      # REQUIRED for perps + the TWAK prize
TWAK_HMAC_SECRET=<from twak setup>    # REQUIRED for perps
CMC_API_KEY=<your CMC Pro key>
GROQ_API_KEY=<your Groq key>
ENABLE_PERPS=true
PERP_LEVERAGE=3.0                     # 2–5x; start at 3
START_MODE=simulation                 # flip to live only after the checks below
```
> Without `TWAK_ACCESS_ID`/`TWAK_HMAC_SECRET` the agent still trades **spot** (local web3 keystore) but **perps are disabled** — you'd lose the down-market edge AND the TWAK special-prize story. Set them.

---

## 1. Register on-chain (required to be scored)

```bash
twak compete register          # submits the on-chain registration tx
twak compete status            # confirm registered
```
Then submit your **agent wallet address** + a short strategy explainer on DoraHacks. The agent will also call registration automatically on first LIVE boot (`register_for_competition`), but doing it by hand first is safer.

## 2. Fund the agent wallet (~$50–70)

Send to the `twak wallet address` from step 0:
- **~$8–12 in BNB** for gas (PancakeSwap swaps + perp txs).
- **~$45–60 in USDT** (BSC, contract `0x55d3…7955`) for trading + perp margin.
- You must hold a **non-zero in-scope balance at the start** — keep most of it in USDT (in-scope) so hour-0 isn't a 0.

Set the sim baseline to match so paper PnL is representative: `SIM_START_USDT` ≈ your USDT.

## 3. ⚠️ Verify perp `--usd` semantics with ONE dust perp (do this once, LIVE)

The CLI's `twak perps open … --usd <X>` is treated by this agent as **margin** (`PERP_USD_IS_MARGIN=true`). Confirm your CLI build agrees before sizing up:

```bash
twak perps open ETH --side long --usd 1 --leverage 2 --chain bsc
twak perps positions --chain bsc     # read the notional/size it actually opened
twak perps close ETH --chain bsc
```
- If the opened **notional ≈ $2** (1 margin × 2x) → leave `PERP_USD_IS_MARGIN=true`. ✅
- If the opened **notional ≈ $1** (i.e. `--usd` WAS the notional) → set `PERP_USD_IS_MARGIN=false`.

## 4. Go live

```bash
python run.py
```
- Open the dashboard (frontend `npm run dev`, default `http://localhost:3000`).
- Confirm the **Wallet** page shows your real funded address and balances.
- Toggle **Simulation → Live** in the header (it double-confirms).
- Watch the **Live Log**: you should see scans, a daily qualifier round-trip, and (in trending tape) spot/perp entries. Perp rows show a `LONG 3x` / `SHORT 3x` badge + liquidation price.

---

## 5. What protects you (the DQ gate)

| Guard | Setting | Effect |
|---|---|---|
| Per-perp margin cap | `PERP_MARGIN_PCT_PER_TRADE=0.12` | one perp ≤ 12% of equity |
| Total perp margin cap | `PERP_TOTAL_MARGIN_PCT=0.30` | all perps ≤ 30% of equity |
| Conservative leverage | `PERP_LEVERAGE=3` | liquidation ~33% away; strategy stops exit at ~3% |
| Liquidation guard | `PERP_LIQ_GUARD_PCT=6` | force-close if mark gets within 6% of liquidation |
| Drawdown ladder | scales size down from 10% DD, stops new risk by ~18% |
| **Soft de-risk** | `FLATTEN_DRAWDOWN_PCT=22` | flatten + pause new entries 2h; **qualifier keeps running** (daily rule kept) |
| **Hard halt** | `DQ_DRAWDOWN_PCT=30` (halts at 27%) | flatten + halt — never reach the 30% DQ cliff |

These keep worst-case drawdown comfortably under the 30% disqualification cap while staying deployed for return.

## 6. Daily during the week
- Glance at the dashboard once or twice a day. The agent self-runs: ≥1 trade/day via the auto-qualifier, regime-adaptive entries, auto-managed exits.
- If you want more/less aggression: raise/lower `PERP_LEVERAGE` (max 5) and `BASE_TRADE_SIZE_USD`. Higher = more return AND more drawdown — mind the 30% gate.
- The **arbiter** auto-suspends any strategy whose live expectancy goes negative over ≥10 trades, so persistent losers prune themselves.

## 7. If something looks wrong
- **No trades firing:** likely CHOP regime (breakouts only fire in confirmed trends — by design). The qualifier still satisfies the daily rule.
- **Perps erroring:** check `twak auth status` and that `TWAK_ACCESS_ID/SECRET` are set; the agent falls back to spot automatically.
- **Equity near a drawdown tier:** expected behavior is auto de-risk; don't override it toward the DQ cap.

---

### Pre-flight checklist
- [ ] `twak --version` works, `twak wallet address` returns your agent address
- [ ] `.env` has TWAK creds + CMC + Groq keys
- [ ] `twak compete register` done + submitted on DoraHacks
- [ ] Wallet funded: BNB (gas) + USDT (trading), non-zero in-scope at start
- [ ] Dust perp opened/closed; `PERP_USD_IS_MARGIN` confirmed
- [ ] Switched to **LIVE**; live log shows scans + qualifier
