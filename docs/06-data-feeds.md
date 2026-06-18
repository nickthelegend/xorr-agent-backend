# 06 — Data & Feeds

The agent reads the market from four live sources, all fail-open (a dead feed never
blocks the trading loop).

## 1. CoinMarketCap — quotes + the skills marketplace
- **Quotes** (`data/cmc_client.py`): batched CMC Pro quotes for the whitelist,
  60s cache, public-Binance fallback.
- **Skills marketplace** (`data/cmc_mcp.py`, `cmc_signals.py`, `funding.py`): the
  agent calls CMC "skill" pipelines over MCP and distills them into the council's
  macro context and the perp funding-fade:
  - `compare_funding_rate_across_venues` → `pack_crowding_regime` (crowded_longs /
    crowded_shorts) + stretch score
  - `detect_funding_rate_regime_shift` → regime state + sign-flip
  - `detect_oi_dark_flow_setup`, `detect_leverage_reset_completion`,
    `assess_liquidation_cascade_risk`, `monitor_market_sentiment_shift`
  - **Funding-fade:** an extreme crowd is the side that's about to get squeezed, so
    a perp signal that **fades** the crowd is boosted ×1.15 and one trading **with**
    it is suppressed ×0.6 (the pangolin/owl funding-fade edge).

## 2. Binance websocket — real-time price feed (`data/ws_feed.py`)
One connection to `!miniTicker@arr` streams every USDT pair's last price ~1×/sec
into an in-memory map. The fast risk/exit monitor overlays these **sub-second**
marks on every tick, so SL/TP/liquidation react quickly instead of waiting on the
~8s REST cache. Fail-open to REST, auto-reconnect.

## 3. Binance Futures websocket — the liquidation tape (`data/liq_feed.py`)
The data klines **don't** contain: the forced-liquidation stream
(`!forceOrder@arr`). `SELL` = a long was liquidated (downward flush); `BUY` = a
short was liquidated (upward flush). Per-symbol rolling history yields **net flow,
z-score, relative-spike, and imbalance** — the inputs to the entire liquidation
strategy family. Live-only (real-time, no history), so liq strategies that need it
are shadow-tested until the live edge is confirmed.

## 4. Fear & Greed — `data/fear_greed.py`
alternative.me index → position-size multiplier (smaller in extreme fear/greed) and
regime input.

## Cascade detection — `data/cascade.py`
The bridge that makes liquidation strategies backtestable: tries the **real liq
feed** first (live), falls back to a **kline proxy** (a z-scored 1h move on a volume
spike ≈ forced flow) so the same strategy runs in both the backtest and live. This
is why backtest numbers reflect the *logic* and live trading uses the *real tape*.

## Health
`GET /health` reports DB status, scheduler loop liveness + heartbeat ages, the WS
price feed (connected, symbols), and the liquidation feed (connected, symbols
tracked) — point an uptime monitor at it during the live week. The frontend
overview shows the same in a "LIVE FEEDS & ENGINE" panel.
