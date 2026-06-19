# 10 — trader.dev: 36,000 Strategies, One Conclusion

> *"please use the api key to access this mcp… see what all strategies it has… fetch all strategies which have best profit and best DD and return, winrate… implement all these in our xorr… see what's the robustness of those strats."*

This is the story of mining a **36,073-strategy** public research database (trader.dev),
porting its highest-quality ideas into XORR, and running every one through **our own
robustness gauntlet** before trusting a cent to them. The headline:

> **trader.dev's best strategies are *all* mean-reversion — an independent, 36k-sample
> confirmation of the exact edge XORR already trades.** We ported the 3 best oscillator
> ideas we didn't already have. **1 of 3 survived our gauntlet and is now live; the other
> 2 are cost-fragile and run as paper shadows.** The database's own Sharpe numbers were
> overfit (13–37 trades each) — our 2× commission stress caught what their stats missed.

---

## 1. How we accessed it

trader.dev exposes both an MCP SSE endpoint and a plain REST API. We used the REST API
with the user's key (`Authorization: Bearer pk_…`):

| Endpoint | What it gives |
|---|---|
| `GET /strategies/stats` | corpus size (**36,073** strategies), coverage |
| `GET /strategies/symbols` | which symbols have strategies |
| `GET /strategies/search?symbol=X&sort=sharpe&limit=N&offset=M` | ranked leaderboard (paginated) |
| `GET /strategies/{id}` | full record incl. **`pineSource`** (the actual TradingView Pine) |

We paginated the realistic-quality leaderboard and the top-Sharpe boards for our majors
(BTC/ETH/SOL/AVAX/BNB), pulling the Pine source of the top ideas to read their exact
mechanics. (Rate-limited; we added delays + pagination to fetch cleanly.)

## 2. What it actually contained

Sorting by realistic, cost-aware quality and reading the top of every board, **the
highest-ranked strategies on our liquid majors were overwhelmingly mean-reversion**,
built on oscillators fading extremes:

- **TSI (True Strength Index)** crossunder/crossover of a threshold → fade
- **Ultimate Oscillator (7/14/28)** oversold/overbought → fade
- **Aroon Oscillator (25)** crossunder/crossover → fade
- Plus Price-ATR Z-Score MR, Ichimoku Kijun-Sen MR, RSI/Stochastic MR (which we already had)

Momentum / breakout / trend-continuation strategies existed in the corpus but **did not
top the realistic-cost boards** on the majors. This is the **same result XORR found
independently 8+ times**: on liquid majors, fading extremes beats chasing momentum
out-of-sample. A 36,000-strategy database arriving at our conclusion is about as strong
an external validation as the edge can get.

## 3. What we ported

We already trade RSI/Stochastic reversion, percentile-flush reversion, VWAP/EMA-deviation
reversion, and liquidation-cascade fades. The **three oscillators we did *not* have** —
and which topped trader.dev's boards — we ported exactly, into
[`strategies/oscillator_mr.py`](../strategies/oscillator_mr.py):

| XORR name | Source idea | Trigger (long = fade down) | Pine params used |
|---|---|---|---|
| `tsi_mr_perp` | TSI MR | `ta.crossunder(tsi, -thresh)` | r=25, s=13, thresh |
| `uo_mr_perp` | Ultimate Osc MR | UO crosses **under** oversold | 7/14/28, OS/OB |
| `aroon_mr_perp` | Aroon Osc MR | Aroon osc crosses **under** −thresh | period 25, thresh 50 |

All three are **long/short perp, regime-gated** (longs only in TREND_UP/CHOP, shorts only
in TREND_DOWN/RISK_OFF/CHOP) and sized through the same risk caps as the rest of the book.
We deliberately did **not** copy trader.dev's tiny entry thresholds (e.g. TSI ±8) — those
fire ~13–37 times in a year, which is what produced their inflated Sharpe. We use wider,
self-consistent thresholds so the strategies actually trade and can be stress-tested.

## 4. The gauntlet result (the important part)

trader.dev reports a Sharpe per strategy, but **those numbers are in-sample and
trade-starved** (13–37 trades). We never trust a backtest stat we didn't stress ourselves,
so all three ran the full [4-way robustness gauntlet](04-robustness.md): out-of-sample
(train BTC 1st-half / test unseen 2nd-half), parameter sensitivity (±15%/±30%, need ≥3/5
positive), **2× commission**, and multi-symbol (ETH+SOL).

```
strategy            OOS     SensBase  trades  Sens   comm2x   ETH     SOL    SURVIVE
aroon_mr_perp      +0.045   +0.050     2.8    5/5   +0.010  +0.043  +0.074   YES
tsi_mr_perp        +0.070   +0.020     1.3    5/5   -0.020  +0.132  +0.134   no
uo_mr_perp         +0.067   +0.035     3.1    5/5   -0.005  +0.113  +0.065   no
```

Read this carefully — it's a model outcome:

- **All three are OOS-positive and 5/5 on parameter sensitivity.** The reversion edge is
  real and not an artifact of one lucky parameter. trader.dev was right about the *direction*.
- **But `tsi_mr_perp` and `uo_mr_perp` go negative at 2× commission** (−0.020, −0.005).
  Their edge per trade is thinner than realistic round-trip cost. That is *exactly* the
  failure mode trader.dev's in-sample Sharpe can't see — and exactly what our gauntlet
  exists to catch.
- **`aroon_mr_perp` stays positive through 2× commission** (+0.010) and on both ETH and
  SOL. It's a modest edge (OOS +0.045 vs our flagship liq_reversion's +0.117) but a
  **robust, cost-surviving** one.

## 5. What we did with the result

| Strategy | Verdict | Status in XORR |
|---|---|---|
| `aroon_mr_perp` | survived all 4 tests | **ENABLED** (`enable_strategy_aroon_mr_perp=True`) — live in the active book |
| `tsi_mr_perp` | OOS+ & robust to params, but fails 2× cost | **SHADOW** — paper-trades live; arbiter promotes only if real fills prove out (≥8 trades, >0.25R) |
| `uo_mr_perp` | OOS+ & robust to params, but fails 2× cost | **SHADOW** — same |

This is the discipline the whole project is built on: **a backtest number — even one from
a 36,000-strategy database — earns capital only after it survives out-of-sample + cost
stress.** We enabled the one that did, and we let the live market be the tiebreaker for the
two that were close. Nothing was enabled on the strength of someone else's Sharpe.

## 6. Honest takeaways

1. **External validation, not new alpha.** trader.dev didn't hand us a 500% strategy; it
   handed us *independent confirmation* that reversion > momentum on majors, plus three
   clean oscillator implementations. The big-return strategies remain
   [our compounding reversion book](08-performance-strategies.md).
2. **Their stats are optimistic.** In-sample Sharpe on 13–37 trades is not a live edge.
   2/3 of even the "best" ideas died at realistic cost. Always re-test.
3. **Aroon adds genuine diversification.** It's a different signal (time-since-extreme)
   than our price/volume/percentile fades, so it decorrelates the book a little — useful
   for the drawdown gate even at a modest per-trade edge.

> Reproduce: `python -m backtest.robustness --all` (gauntlet) — `aroon_mr_perp` shows `YES`,
> `tsi_mr_perp` / `uo_mr_perp` show `no`. Unit coverage:
> `python -m pytest tests/test_oscillator_mr.py -q`.

---

## 7. Second pass — the BROADER survey, scored for SPOT

After the spot-only pivot we went back for **all** the strategies, not just the top-3
oscillators, specifically asking *"which can be implemented as long-only spot?"* We pulled
**283 distinct strategies** across four sort orders (sharpe/profit/sortino/winrate).

**What the broad population actually looks like** (keyword tally on names): EMA 96 ·
breakout 77 · MACD 33 · ADX 24 · RSI 16 · mean/reversion 19 · stoch 7. So the bulk is
**trend/breakout**, whose *long* side is spot-viable — the opposite skew from the Sharpe-only
top (which was pure mean-reversion).

**Two hard filters killed most of it:**
1. **Off-instrument.** The top-Sharpe board is dominated by **XAUUSD (gold) / PAXG / forex**
   session-breakouts (Sharpe 36–45 — in-sample fantasy) that we *cannot trade* (not BEP-20).
2. **Overfit parameter soup.** The top *crypto* names are `Evo_ETHUSDT_Gen16`, `…_Gen35`,
   etc. — genetically-evolved indicator combos with 1–4% return and 0.4–1% drawdown over
   tiny windows. Not portable archetypes, just tuned coefficient sets.

**The genuinely new, crypto, long-spot-viable archetypes we didn't already have** → ported
into [`strategies/spot_ports.py`](../strategies/spot_ports.py), then run through our `--spot`
(long-only, 1×) KNOWN/UNKNOWN backtest:

| Port | Source | KNOWN | UNKNOWN | Verdict |
|---|---|--:|--:|---|
| `stochrsi_mr_perp` | StochRSI MR (ADA/ETH/NEAR 4h, win 65–80%) | +49% | **+8%** | ✅ **ENABLED** — clean new MR archetype, holds OOS |
| `adx_trend_perp` | ADX/DMI trend (clean reimpl of a Sharpe-0.43 mess) | +1% | +9% | 🟡 **SHADOW** — OOS+ but inconsistent |
| `bb_breakout_perp` | "BB Breakout Opt" (ETH 1h) | +11% | **−13%** | ❌ **SHADOW** — fails OOS (breakout overfits) |

**The lesson repeats a 10th time:** the *mean-reversion* port (`stochrsi_mr`) survives
out-of-sample; the *breakout/momentum* port (`bb_breakout`) overfits and dies on unseen
data; the *trend* port (`adx_trend`) only squeaks through. We enabled the one MR winner,
shadowed the rest. Most of trader.dev's "best" simply **can't be traded by us** (gold/forex)
or **isn't a real edge** (evolved overfit) — and our `--spot` re-test, not their Sharpe, made
the call.

> Reproduce: `python -X utf8 -m backtest.perf --spot --all` (look for the three `*_perp`
> rows above). Unit coverage: `python -m pytest tests/test_spot_ports.py -q`.
