"""Focused long/short validation harness for the donchian_perp strategy.

Replays REAL Binance 1h klines through the REAL DonchianPerpStrategy.evaluate()
with direction-aware, leverage- and fee-realistic fills, and compares two modes
on the SAME window:

    long_only  — take only LONG breakouts (what the spot book can do today)
    long_short — take LONG breakouts AND SHORT breakdowns (the new perp book)

The point is to show the SHORT side adds positive expectancy in a down/choppy
tape — the structural edge that turns "breakeven in a down week" into a win.
Expectancy (R) is leverage-invariant; total-return% and max-drawdown% are shown
at the configured leverage so the DQ-gate profile is visible too.

Run:  python -X utf8 -m backtest.perp_backtest --days 80 --leverage 3
"""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Optional

from core.types import Candle, MarketContext
from strategies.donchian_perp import DonchianPerpStrategy
from strategies.supertrend_perp import SupertrendPerpStrategy
from strategies.salamander_perp import SalamanderPerpStrategy
from strategies.volsqueeze_perp import VolSqueezePerpStrategy
from strategies.rsi_div_perp import RsiDivPerpStrategy
from backtest.data_loader import get_klines_for_backtest
from config import settings

STRATS = {
    "donchian_perp": DonchianPerpStrategy,
    "supertrend_perp": SupertrendPerpStrategy,
    "salamander_perp": SalamanderPerpStrategy,
    "volsqueeze_perp": VolSqueezePerpStrategy,
    "rsi_div_perp": RsiDivPerpStrategy,
}

PERP_FEE = 0.0006          # taker fee per side on notional
MAX_HOLD_BARS = 12         # 12 x 1h = 12h (matches monitor max_hold for perp)
PER_TRADE_MARGIN_PCT = 0.12
TOTAL_MARGIN_PCT = 0.30
MAX_CONCURRENT = 4


def _ema_series(closes: List[float], period: int) -> List[float]:
    if not closes:
        return []
    k = 2.0 / (period + 1.0)
    out = [closes[0]]
    for p in closes[1:]:
        out.append((p - out[-1]) * k + out[-1])
    return out


@dataclass
class BTPos:
    symbol: str
    direction: str
    entry_idx: int
    entry: float
    stop: float
    tp: float
    init_stop: float
    margin: float
    leverage: float
    size: float
    tp1: bool = False
    peak: float = 0.0
    trough: float = 1e18


@dataclass
class Mode:
    name: str
    allow_short: bool
    cash: float = 100.0
    equity: float = 100.0
    peak_equity: float = 100.0
    max_dd: float = 0.0
    open: List[BTPos] = field(default_factory=list)
    trades: List[dict] = field(default_factory=list)

    def open_margin(self) -> float:
        return sum(p.margin for p in self.open)


def candles_from(df) -> List[Candle]:
    out = []
    ot = df["open_time"].tolist(); o = df["open"].tolist(); h = df["high"].tolist()
    l = df["low"].tolist(); c = df["close"].tolist(); v = df["volume"].tolist()
    for i in range(len(ot)):
        out.append(Candle(ts=datetime.fromtimestamp(ot[i] / 1000.0, timezone.utc),
                          open=o[i], high=h[i], low=l[i], close=c[i], volume=v[i]))
    return out


async def run(days: int, leverage: float, strat_name: str = "donchian_perp"):
    raw = (getattr(settings, "perp_symbols", "") or "")
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]

    # Load per-symbol 1h candles + BTC daily for regime.
    data: Dict[str, List[Candle]] = {}
    for sym in symbols:
        df = await get_klines_for_backtest(sym, "1h", days=days)
        if df is not None and not df.empty and len(df) > 60:
            data[sym] = candles_from(df)
    btc_d = await get_klines_for_backtest("BTC", "1d", days=days + 5)
    btc_closes = btc_d["close"].tolist() if btc_d is not None and not btc_d.empty else []
    btc_times = btc_d["open_time"].tolist() if btc_closes else []
    btc_ema20 = _ema_series(btc_closes, 20) if btc_closes else []

    def regime_at(ts_ms: int) -> str:
        if not btc_closes:
            return "CHOP"
        idx = 0
        for i, t in enumerate(btc_times):
            if t <= ts_ms:
                idx = i
            else:
                break
        if idx < 1:
            return "CHOP"
        curr = btc_closes[idx]; prev = btc_closes[idx - 1]; ema = btc_ema20[idx]
        ret = (curr - prev) / prev if prev else 0.0
        if curr > ema and ret > 0.005:
            return "TREND_UP"
        if curr < ema and ret < -0.005:
            return "TREND_DOWN"
        return "CHOP"

    if not data:
        print("No data loaded — check network/klines.")
        return

    strat = STRATS.get(strat_name, DonchianPerpStrategy)()
    # Align all symbols on a shared 1h timeline by index (they share ~same bars).
    n = min(len(c) for c in data.values())
    modes = [Mode("long_only", allow_short=False), Mode("long_short", allow_short=True)]

    def ctx_for(regime, last_price_map):
        m = MarketContext(timestamp=datetime.now(timezone.utc), fear_greed_value=50,
                          fear_greed_label="N", btc_dominance=55, total_market_cap_usd=2.5e12,
                          total_market_cap_change_24h=0, bnb_price_usd=600, regime=regime)
        return m

    regime_counts = {"TREND_UP": 0, "TREND_DOWN": 0, "CHOP": 0}

    for i in range(55, n):
        # regime from the current bar's timestamp (use BTC if present else any symbol)
        any_sym = next(iter(data))
        ts_ms = int(data[any_sym][i].ts.timestamp() * 1000)
        regime = regime_at(ts_ms)
        regime_counts[regime] = regime_counts.get(regime, 0) + 1

        for mode in modes:
            # --- manage exits on this bar ---
            still = []
            for p in mode.open:
                bar = data[p.symbol][i]
                hold = i - p.entry_idx
                exit_px = None; reason = None
                p.peak = max(p.peak, bar.high)
                p.trough = min(p.trough, bar.low)
                fav = ((bar.close - p.entry) / p.entry) * (1 if p.direction == "long" else -1) * 100.0

                # time stop
                if hold >= MAX_HOLD_BARS:
                    exit_px = bar.close; reason = "TIME"
                # stop loss (SL checked before TP — conservative)
                elif not p.tp1 and ((p.direction == "long" and bar.low <= p.stop) or
                                    (p.direction == "short" and bar.high >= p.stop)):
                    exit_px = p.stop; reason = "SL"
                elif not p.tp1 and ((p.direction == "long" and bar.high >= p.tp) or
                                    (p.direction == "short" and bar.low <= p.tp)):
                    exit_px = p.tp; reason = "TP"
                else:
                    # profit-lock + 3% trail once +2.5%
                    if fav >= 2.5 and not p.tp1:
                        p.tp1 = True
                        if p.direction == "long":
                            p.stop = max(p.stop, p.entry * 1.005, p.peak * 0.97)
                        else:
                            p.stop = min(p.stop, p.entry * 0.995, p.trough * 1.03)
                    elif p.tp1:
                        if p.direction == "long":
                            p.stop = max(p.stop, p.peak * 0.97)
                            if bar.low <= p.stop:
                                exit_px = p.stop; reason = "TRAIL"
                        else:
                            p.stop = min(p.stop, p.trough * 1.03)
                            if bar.high >= p.stop:
                                exit_px = p.stop; reason = "TRAIL"

                if exit_px is None:
                    still.append(p); continue

                # realize (incl. funding carry over the hold; bars == hours)
                gross = p.size * (exit_px - p.entry) * (1 if p.direction == "long" else -1)
                fee = (p.size * p.entry + p.size * exit_px) * PERP_FEE
                funding = (p.size * exit_px) * settings.perp_funding_rate_8h * (hold / 8.0)
                pnl = gross - fee - funding
                mode.cash += p.margin + pnl
                risk = p.size * abs(p.entry - p.init_stop)
                r = pnl / risk if risk > 0 else 0.0
                mode.trades.append({"symbol": p.symbol, "dir": p.direction, "pnl": pnl,
                                    "r": r, "reason": reason})
            mode.open = still

            # --- entries on this bar ---
            for sym, candles in data.items():
                if any(p.symbol == sym for p in mode.open):
                    continue
                if len(mode.open) >= MAX_CONCURRENT:
                    break
                hist = candles[max(0, i - 80):i + 1]
                sig = await strat.evaluate(sym, [], hist, ctx_for(regime, None))
                if not sig:
                    continue
                if sig.direction == "short" and not mode.allow_short:
                    continue
                # size by margin under caps
                per_cap = PER_TRADE_MARGIN_PCT * mode.equity
                room = max(0.0, TOTAL_MARGIN_PCT * mode.equity - mode.open_margin())
                margin = min(per_cap, room, mode.cash)
                if margin < settings.perp_min_margin_usd:
                    continue
                entry = candles[i].close
                size = margin * leverage / entry
                if sig.direction == "long":
                    stop = entry * (1 - sig.stop_loss_pct / 100.0)
                    tp = entry * (1 + sig.take_profit_pct / 100.0)
                else:
                    stop = entry * (1 + sig.stop_loss_pct / 100.0)
                    tp = entry * (1 - sig.take_profit_pct / 100.0)
                fee = size * entry * PERP_FEE
                mode.cash -= (margin + fee)
                mode.open.append(BTPos(symbol=sym, direction=sig.direction, entry_idx=i,
                                       entry=entry, stop=stop, tp=tp, init_stop=stop,
                                       margin=margin, leverage=leverage, size=size,
                                       peak=entry, trough=entry))

            # --- mark equity / drawdown ---
            eq = mode.cash
            for p in mode.open:
                price = data[p.symbol][i].close
                upnl = p.size * (price - p.entry) * (1 if p.direction == "long" else -1)
                eq += max(0.0, p.margin + upnl)
            mode.equity = eq
            mode.peak_equity = max(mode.peak_equity, eq)
            dd = (mode.peak_equity - eq) / mode.peak_equity * 100.0 if mode.peak_equity > 0 else 0.0
            mode.max_dd = max(mode.max_dd, dd)

    # --- report ---
    total_bars = n - 55
    print(f"\nStrategy: {strat_name} | Window: ~{days}d, {len(data)} perp majors, leverage {leverage:.0f}x, {total_bars} 1h bars")
    print(f"Regime mix: TREND_UP {regime_counts['TREND_UP']}, TREND_DOWN {regime_counts['TREND_DOWN']}, CHOP {regime_counts['CHOP']}")
    print("=" * 78)
    for mode in modes:
        # close any still-open at last close
        for p in mode.open:
            price = data[p.symbol][n - 1].close
            gross = p.size * (price - p.entry) * (1 if p.direction == "long" else -1)
            fee = (p.size * p.entry + p.size * price) * PERP_FEE
            pnl = gross - fee
            mode.cash += p.margin + pnl
            risk = p.size * abs(p.entry - p.init_stop)
            mode.trades.append({"symbol": p.symbol, "dir": p.direction, "pnl": pnl,
                                "r": (pnl / risk if risk > 0 else 0.0), "reason": "EOD"})
        ts = mode.trades
        n_t = len(ts)
        if n_t == 0:
            print(f"{mode.name:11s}: no trades"); continue
        wins = [t for t in ts if t["pnl"] > 0]
        exp_r = sum(t["r"] for t in ts) / n_t
        ret_pct = (mode.cash - 100.0)  # started at 100
        longs = [t for t in ts if t["dir"] == "long"]
        shorts = [t for t in ts if t["dir"] == "short"]
        def er(group): return (sum(t["r"] for t in group) / len(group)) if group else 0.0
        print(f"{mode.name:11s}: trades={n_t:3d}  win%={len(wins)/n_t*100:5.1f}  "
              f"expectancy={exp_r:+.3f}R  totalPnL={ret_pct:+.2f}%  maxDD={mode.max_dd:4.1f}%")
        print(f"             longs={len(longs):3d} ({er(longs):+.3f}R)   shorts={len(shorts):3d} ({er(shorts):+.3f}R)")
    print("=" * 78)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=80)
    ap.add_argument("--leverage", type=float, default=3.0)
    ap.add_argument("--strat", type=str, default="donchian_perp",
                    choices=list(STRATS.keys()) + ["all"])
    args = ap.parse_args()
    if args.strat == "all":
        for name in STRATS:
            asyncio.run(run(args.days, args.leverage, name))
    else:
        asyncio.run(run(args.days, args.leverage, args.strat))
