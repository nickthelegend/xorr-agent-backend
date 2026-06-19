# XORR — BNB Hack Track 1 Go-Live Runbook

**Competition:** BNB Hack: AI Trading Agent Edition (CoinMarketCap × Trust Wallet × BNB Chain)
**Submit by:** June 21 · **Live trading:** June 22–28 · **Judged on:** total return, with a **~30% max-drawdown cap = disqualification**. Min **1 trade/day**, only the **149 eligible BEP-20 tokens** count, hold a non-zero in-scope balance at the start, keep a **>$1 portfolio** every hour.

> ⚠️ **SPOT ONLY.** This competition permits **spot trading only — no perpetuals.** The
> agent runs in `SPOT_ONLY=true`: it trades long-only spot swaps on PancakeSwap and
> **never opens a perp** (hard-guarded at the executor). No leverage → **no liquidation
> risk at all**, so the 30% drawdown DQ gate is far easier to respect.

This agent is a **regime-adaptive long-only spot** system. Its edge is **mean reversion —
buying oversold liquidation flushes on the liquid majors and selling into the bounce:**
- **Reversion book** (the proven `liq_*` / `adaptive_percentile_reversion` / `aroon_mr`
  family) runs on ETH/BNB/XRP/DOGE/ADA/AVAX/LINK, taking only the **LONG (buy-the-dip)**
  side as 1x spot. In a falling tape these stay flat (cash = capital preservation) rather
  than catching a falling knife.
- **Spot momentum/breakout book** (`donchian_breakout` ⭐, `trend_follow`, `capitulation`,
  `whale_flow`, `news_catalyst`) for up-trending tape.
- **Honest trade-off vs the old long/short perp design:** dropping shorts + leverage lowers
  the return ceiling (single-digit % over a week, not the leveraged numbers) but removes
  blow-up risk entirely. We optimize for *return within the DQ gate*, not raw upside.

---

## Going live (spot — simple)
Spot needs **only a funded wallet — no TWAK creds.** The agent signs real PancakeSwap V2
swaps locally from `data_store/agent_keystore.json`. Fund the wallet → switch to LIVE → it
trades the spot reversion + momentum book on-chain. (Verified: BSC connects, real
PancakeSwap quotes work.)

> TWAK is now **optional** — it is *not* required to trade in spot-only mode. It only
> matters if you also want to chase the **TWAK special prize** (you can route the same spot
> swaps through `twak swap` instead of the web3 keystore). Perps are disabled regardless.

The **Wallet page → GO-LIVE READINESS** panel (and `GET /api/readiness`, now reporting
`tradingVenue: "spot"`) shows a live checklist of exactly what's done and what's missing.

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

`.env` (copy from `.env.example`) — the lines that matter for LIVE (spot-only):
```
SPOT_ONLY=true                        # competition is spot-only; perps hard-disabled
ENABLE_PERPS=false
CMC_API_KEY=<your CMC Pro key>
GROQ_API_KEY=<your Groq key>
START_MODE=simulation                 # flip to live only after the checks below
# TWAK is OPTIONAL in spot-only mode (only for the special prize):
# TWAK_PASSWORD / TWAK_ACCESS_ID / TWAK_HMAC_SECRET=<from twak setup>
```
> In spot-only mode TWAK creds are **not needed to trade** — spot signs through the local
> web3 keystore. Set them only if you also want to pursue the TWAK special prize.

---

## 1. Fund the agent wallet (~$50 total)

**Your agent wallet (self-custody keystore, already generated):**
```
0x3551f68748AACDd77d28a4149C014f8FFbb95f91
```
This is the one to fund — it signs every spot swap locally (no TWAK needed). On a **$50**
budget, send to it on **BNB Smart Chain (BEP-20)**:
- **~$6–8 in BNB** for gas — BSC gas is dirt cheap (a swap is ~$0.01–0.05, ERC-8004
  registration was metered at **~$0.01**), so this is plenty for the whole week.
- **~$42–44 in USDT** (BSC, contract `0x55d3…7955`) for trading.
- Hold a **non-zero in-scope balance at the start** — keep most of it in USDT (in-scope) so hour-0 isn't a 0.

`SIM_START_USDT` already defaults to **42** to mirror this. (Confirm the exact address on the
Wallet page → fundable address before sending.)

## 2. Register on-chain (required to be scored)

**One command does BOTH registrations**, signed locally by the same wallet that trades —
**no TWAK creds needed** (the competition contract is a permissionless `register()`):
```bash
python -m scripts.register_agent           # DRY RUN — shows plans + gas, sends nothing
python -m scripts.register_agent --send     # broadcasts both (each ~$0.003–0.01 gas)
```
1. **ERC-8004 Identity Registry** (BRC8004, BSC `0xfA09…59D7`) — mints the agent identity
   NFT, pointing to [`agent_card.json`](agent_card.json).
2. **Competition contract** (`0x212c…aed5`) — enrolls the wallet for scoring (registration
   deadline **2026-06-25**).

Re-runs are safe — each step short-circuits if already registered. Then submit your **agent
wallet address** + a short strategy explainer on DoraHacks.

> TWAK's `twak compete register` would register a *different* (TWAK-managed) wallet and needs
> API creds — so we register the funded/trading wallet directly via web3 instead.

## 3. ✅ No perp verification needed (spot-only)

The old dust-perp `--usd`-semantics check **does not apply** — the agent never opens a perp
(`SPOT_ONLY=true`, and `open_perp` fails closed). There is **no leverage and no liquidation
risk** to verify. Spot swaps are the proven, already-verified path. Skip straight to go-live.

## 4. Go live

```bash
python run.py --spot     # spot-only (the competition mode)
# python run.py --perps  # the perp engine is intact — selectable if perps are ever allowed
# python run.py          # venue from .env (SPOT_ONLY/ENABLE_PERPS)
```
- Open the dashboard (frontend `npm run dev`, default `http://localhost:3000`).
- Confirm the **Wallet** page shows your real funded address and balances, and **GO-LIVE
  READINESS** reads `tradingVenue: spot`.
- Toggle **Simulation → Live** in the header (it double-confirms).
- Watch the **Live Log**: scans, a daily qualifier round-trip, and (in the right tape) spot
  `BUY` entries from the reversion + breakout book. Every row is a 1x **SPOT** swap — no
  leverage badge, no liquidation price.

---

## 5. What protects you (the DQ gate)

Spot-only removes the entire leverage/liquidation failure mode, so the DQ gate is much
easier to hold. What's left:

| Guard | Setting | Effect |
|---|---|---|
| **No leverage / no liquidation** | `SPOT_ONLY=true` | you own the token outright; worst case is the asset's own drawdown, never a wipeout |
| Per-trade size cap | `BASE_TRADE_SIZE_USD` × confidence/F&G/DD | each spot buy is a small fraction of equity |
| Long-only discipline | reversion fires only in TREND_UP/CHOP | in a falling tape the book sits in USDT (cash) instead of buying the knife |
| Drawdown ladder | scales size down from 10% DD, stops new risk by ~18% |
| **Soft de-risk** | `FLATTEN_DRAWDOWN_PCT=22` | flatten to USDT + pause new entries 2h; **qualifier keeps running** (daily rule kept) |
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
