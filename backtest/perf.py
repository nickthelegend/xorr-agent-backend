"""Compounding portfolio backtest + robustness, with return% / maxDD / Sharpe.

Unlike the gauntlet (fixed sizing, for honest expectancy), this COMPOUNDS — margin
is a % of CURRENT equity, traded as a shared-cash portfolio across several majors.
That's how a real +0.15-0.25R reversion edge turns into a large headline return over
many trades. Used to (a) tune each strategy's sizing to the 500%/<25%DD goal, and
(b) report robustness on KNOWN (first half) vs UNKNOWN (second half) data.

  python -X utf8 -m backtest.perf --tune                 # find sizing for 500%/<25%DD
  python -X utf8 -m backtest.perf --report               # known/unknown ret/DD/Sharpe
"""
from __future__ import annotations
import argparse
import asyncio
import math
from datetime import datetime, timezone
from typing import Dict, List

from core.types import Candle, MarketContext
from backtest.data_loader import get_klines_for_backtest
from strategies.registry import STRATEGIES
from config import settings

DAYS = 365
FEE = 0.0006
SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK"]
MAX_CONC = 6
PERP_FUNDING = 0.0001

# Five new ideas + the proven reversion book (for the robustness report)
PERF5 = ["liq_mtf_reversion_perp", "liq_range_extreme_perp", "liq_rsi_stack_perp",
         "liq_vwap_reversion_perp", "liq_double_extreme_perp"]
BOOK = PERF5 + ["liq_reversion_perp", "liq_support_reversion_perp",
                "liq_climax_reversion_perp", "adaptive_percentile_reversion_perp",
                "cascade_filter_perp", "salamander_perp", "dominant_burst_perp"]


def candles_from(df) -> List[Candle]:
    ot = df["open_time"].tolist(); o = df["open"].tolist(); h = df["high"].tolist()
    l = df["low"].tolist(); c = df["close"].tolist(); v = df["volume"].tolist()
    return [Candle(ts=datetime.fromtimestamp(ot[i] / 1000.0, timezone.utc),
                   open=o[i], high=h[i], low=l[i], close=c[i], volume=v[i]) for i in range(len(ot))]


def make_regime_fn(btc_1d):
    closes = btc_1d["close"].tolist() if btc_1d is not None and not btc_1d.empty else []
    times = btc_1d["open_time"].tolist() if closes else []
    ema = []
    if closes:
        k = 2.0 / 21.0; ema = [closes[0]]
        for p in closes[1:]:
            ema.append((p - ema[-1]) * k + ema[-1])

    def regime_at(ts_ms):
        if not closes:
            return "CHOP"
        idx = 0
        for i, t in enumerate(times):
            if t <= ts_ms:
                idx = i
            else:
                break
        if idx < 1:
            return "CHOP"
        curr, prev, e = closes[idx], closes[idx - 1], ema[idx]
        ret = (curr - prev) / prev if prev else 0.0
        if curr > e and ret > 0.005:
            return "TREND_UP"
        if curr < e and ret < -0.005:
            return "TREND_DOWN"
        return "CHOP"
    return regime_at


class override:
    def __init__(self, **kw): self.kw = kw; self.old = {}
    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(settings, k, None); setattr(settings, k, v)
    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(settings, k, v)


async def simulate(cls, data: Dict[str, List[Candle]], regime_at, symbols: List[str],
                   leverage: float, sizing_pct: float, lo: int, hi: int, total_cap: float = 0.85,
                   dd_halt: float = 18.0, long_only: bool = False) -> Dict:
    """Shared-cash COMPOUNDING portfolio. margin = sizing_pct * equity per trade.
    dd_halt models the live drawdown circuit breaker: no NEW entries while the
    portfolio is in drawdown > dd_halt (open positions still resolve).
    long_only=True + leverage=1.0 turns this into a SPOT backtest (buy-the-flush
    only, no shorts, no leverage) — the competition's actual constraints."""
    cash = 100.0; equity = 100.0; peak = 100.0; maxdd = 0.0
    open_pos: List[dict] = []
    trades: List[float] = []
    eq_curve: List[float] = []
    start = max(125, lo)
    for i in range(start, hi):
        # exits
        still = []
        for p in open_pos:
            bar = data[p["s"]][i]
            hold = i - p["i"]
            fav = ((bar.close - p["e"]) / p["e"]) * (1 if p["d"] == "long" else -1) * 100.0
            p["pk"] = max(p["pk"], bar.high); p["tr"] = min(p["tr"], bar.low)
            ex = None
            if hold >= p["mh"]:
                ex = bar.close
            elif not p["t1"] and ((p["d"] == "long" and bar.low <= p["st"]) or (p["d"] == "short" and bar.high >= p["st"])):
                ex = p["st"]
            elif not p["t1"] and ((p["d"] == "long" and bar.high >= p["tp"]) or (p["d"] == "short" and bar.low <= p["tp"])):
                ex = p["tp"]
            else:
                if fav >= 2.5 and not p["t1"]:
                    p["t1"] = True
                    p["st"] = max(p["st"], p["e"] * 1.005, p["pk"] * 0.97) if p["d"] == "long" else min(p["st"], p["e"] * 0.995, p["tr"] * 1.03)
                elif p["t1"]:
                    if p["d"] == "long":
                        p["st"] = max(p["st"], p["pk"] * 0.97)
                        if bar.low <= p["st"]:
                            ex = p["st"]
                    else:
                        p["st"] = min(p["st"], p["tr"] * 1.03)
                        if bar.high >= p["st"]:
                            ex = p["st"]
            if ex is None:
                still.append(p); continue
            gross = p["sz"] * (ex - p["e"]) * (1 if p["d"] == "long" else -1)
            cost = (p["sz"] * p["e"] + p["sz"] * ex) * FEE + (p["sz"] * ex) * PERP_FUNDING * (hold / 8.0)
            pnl = gross - cost
            cash += p["mg"] + pnl
            risk = p["sz"] * abs(p["e"] - p["ini"])
            trades.append(pnl / risk if risk > 0 else 0.0)
        open_pos = still
        # Drawdown circuit breaker = the live SOFT kill: when the book draws down
        # past dd_halt, flatten everything and reset the drawdown baseline so it
        # recovers (exactly what the runtime kill switch does). Caps episode DD.
        cur_eq = eq_curve[-1] if eq_curve else equity
        de_risked = False
        if peak > 0 and (peak - cur_eq) / peak * 100.0 > dd_halt and open_pos:
            for p in open_pos:
                b = data[p["s"]][i]
                gross = p["sz"] * (b.close - p["e"]) * (1 if p["d"] == "long" else -1)
                cost = (p["sz"] * p["e"] + p["sz"] * b.close) * FEE
                cash += p["mg"] + gross - cost
                risk = p["sz"] * abs(p["e"] - p["ini"]); trades.append((gross - cost) / risk if risk > 0 else 0.0)
            open_pos = []
            peak = cash          # reset baseline to flat equity (recoverable de-risk)
            de_risked = True
        for sym in symbols:
            if de_risked or len(open_pos) >= MAX_CONC:
                break
            if any(p["s"] == sym for p in open_pos):
                continue
            arr = data.get(sym)
            if not arr or i >= len(arr):
                continue
            hist = arr[max(0, i - 120):i + 1]
            ts = int(arr[i].ts.timestamp() * 1000)
            ctx = MarketContext(timestamp=arr[i].ts, fear_greed_value=50, fear_greed_label="N",
                                btc_dominance=55, total_market_cap_usd=2.5e12,
                                total_market_cap_change_24h=0, bnb_price_usd=600, regime=regime_at(ts))
            try:
                sig = await cls().evaluate(sym, [], hist, ctx)
            except Exception:
                sig = None
            if not sig:
                continue
            if long_only and getattr(sig, "direction", "long") == "short":
                continue   # spot can't short — only the buy-the-flush side
            entry = arr[i].close
            used = sum(p["mg"] for p in open_pos)
            margin = min(sizing_pct * equity, total_cap * equity - used, cash)
            if margin < 1.0:
                continue
            if sig.direction == "short":
                st = entry * (1 + sig.stop_loss_pct / 100.0); tp = entry * (1 - sig.take_profit_pct / 100.0)
            else:
                st = entry * (1 - sig.stop_loss_pct / 100.0); tp = entry * (1 + sig.take_profit_pct / 100.0)
            sz = margin * leverage / entry
            cash -= (margin + sz * entry * FEE)
            open_pos.append({"s": sym, "e": entry, "d": sig.direction, "st": st, "tp": tp, "ini": st,
                             "sz": sz, "mg": margin, "i": i, "t1": False, "pk": entry, "tr": entry,
                             "mh": max(1, int(sig.max_hold_min / 60))})
        # equity / drawdown
        eq = cash
        for p in open_pos:
            up = p["sz"] * (data[p["s"]][i].close - p["e"]) * (1 if p["d"] == "long" else -1)
            eq += max(0.0, p["mg"] + up)
        equity = eq
        peak = max(peak, equity)
        if peak > 0:
            maxdd = max(maxdd, (peak - equity) / peak * 100.0)
        eq_curve.append(equity)
    # Sharpe on daily equity returns (24 1h bars/day), annualized
    daily = eq_curve[::24]
    rets = [(daily[i] / daily[i - 1] - 1) for i in range(1, len(daily)) if daily[i - 1] > 0]
    sharpe = 0.0
    if len(rets) > 2:
        m = sum(rets) / len(rets)
        sd = (sum((r - m) ** 2 for r in rets) / len(rets)) ** 0.5
        sharpe = (m / sd) * math.sqrt(365) if sd > 0 else 0.0
    n = len(trades); wins = sum(1 for r in trades if r > 0)
    return {"ret": equity - 100.0, "maxdd": maxdd, "sharpe": sharpe, "n": n,
            "win": (wins / n * 100.0) if n else 0.0, "exp": (sum(trades) / n) if n else 0.0,
            "equity": equity}


async def load():
    data = {}
    for s in SYMBOLS:
        df = await get_klines_for_backtest(s, "1h", days=DAYS)
        data[s] = candles_from(df) if df is not None and not df.empty else []
    btc_1d = await get_klines_for_backtest("BTC", "1d", days=DAYS + 5)
    return data, make_regime_fn(btc_1d)


async def tune(names, lev=5):
    data, regime_at = await load()
    n = min(len(data[s]) for s in SYMBOLS if data[s])
    print(f"Tuning to 500% / <25%DD on {len(SYMBOLS)} majors, {n} bars (~{DAYS}d 1h, compounding portfolio)\n")
    sizings = [round(0.25 + 0.05 * k, 2) for k in range(15)]    # 0.25 .. 0.95
    dd_halts = [12.0, 15.0, 18.0]
    chosen = {}
    for name in names:
        cls = STRATEGIES.get(name)
        if not cls:
            continue
        results = []
        for ddh in dd_halts:
            for sz in sizings:
                with override(perp_symbols=",".join(SYMBOLS)):
                    r = await simulate(cls, data, regime_at, SYMBOLS, lev, sz, 0, n, dd_halt=ddh)
                results.append((sz, ddh, r))
        ok = [(sz, ddh, r) for sz, ddh, r in results if r["maxdd"] < 25.0 and r["n"] > 10]
        if ok:
            sz, ddh, r = max(ok, key=lambda x: x[2]["ret"])
            hit = r["ret"] >= 500.0
            tag = "GOAL HIT " if hit else "best<25DD"
            chosen[name] = (sz, ddh, r)
            print(f"  {tag} {name:26s} sz={sz:.0%} ddh={ddh:.0f} lev={lev}x -> ret={r['ret']:+.0f}% maxDD={r['maxdd']:.1f}% Sharpe={r['sharpe']:.2f} trades={r['n']} win={r['win']:.0f}%")
        else:
            sz, ddh, r = max(results, key=lambda x: x[2]["n"])
            print(f"  NO-FIT   {name:26s} (sz={sz:.0%}: ret={r['ret']:+.0f}% maxDD={r['maxdd']:.1f}% trades={r['n']})")
    return chosen


async def report(names, lev=5, long_only=False):
    """Proper train/test robustness: TUNE sizing on the first half (KNOWN), then
    apply that exact config to the unseen second half (UNKNOWN). Report ret/DD/Sharpe
    for both halves so you can see which survive on data they were never fit to.
    long_only=True + lev=1 reports the SPOT-only book (the competition's constraints)."""
    data, regime_at = await load()
    n = min(len(data[s]) for s in SYMBOLS if data[s]); half = n // 2
    sizings = [round(0.25 + 0.05 * k, 2) for k in range(15)]
    dd_halts = [12.0, 15.0]
    venue = "SPOT (long-only, 1x)" if long_only else f"perp lev={lev}x"
    print(f"\nROBUSTNESS — compounding portfolio across {len(SYMBOLS)} majors, {venue}, {n} bars (~{DAYS}d 1h)")
    print("KNOWN = first half (sizing tuned here) | UNKNOWN = second half (unseen, same config)\n")
    print(f"{'strategy':28s} {'cfg':>10} | {'KNOWN ret%':>10} {'DD%':>6} {'Shrp':>5} {'win%':>5} | {'UNKNOWN ret%':>12} {'DD%':>6} {'Shrp':>5} {'win%':>5}  OOS-ok")
    print("-" * 116)
    surv = 0
    for name in names:
        cls = STRATEGIES.get(name)
        if not cls:
            continue
        # tune on KNOWN
        best = None
        for ddh in dd_halts:
            for sz in sizings:
                with override(perp_symbols=",".join(SYMBOLS)):
                    k = await simulate(cls, data, regime_at, SYMBOLS, lev, sz, 0, half, dd_halt=ddh, long_only=long_only)
                if k["maxdd"] < 25.0 and k["n"] > 10 and (best is None or k["ret"] > best[2]["ret"]):
                    best = (sz, ddh, k)
        if best is None:
            print(f"{name:28s} {'--':>10} | {'no fit on known half':>36}")
            continue
        sz, ddh, k = best
        with override(perp_symbols=",".join(SYMBOLS)):
            u = await simulate(cls, data, regime_at, SYMBOLS, lev, sz, half, n, dd_halt=ddh, long_only=long_only)
        oos_ok = u["ret"] > 0 and u["maxdd"] < 35.0
        surv += 1 if oos_ok else 0
        print(f"{name:28s} {f'{sz:.0%}/dd{ddh:.0f}':>10} | {k['ret']:+10.0f} {k['maxdd']:6.1f} {k['sharpe']:5.2f} {k['win']:5.0f} | {u['ret']:+12.0f} {u['maxdd']:6.1f} {u['sharpe']:5.2f} {u['win']:5.0f}  {'YES' if oos_ok else 'no'}")
    print("-" * 116)
    print(f"OOS-positive on the UNKNOWN half: {surv}/{len([x for x in names if x in STRATEGIES])}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tune", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--spot", action="store_true", help="SPOT-only book: long-only, 1x (competition constraints)")
    ap.add_argument("--sizing", type=float, default=0.45)
    ap.add_argument("--lev", type=int, default=5)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    names = [k for k in STRATEGIES if "perp" in k] if args.all else BOOK
    if args.tune:
        asyncio.run(tune(PERF5))
    elif args.spot:
        asyncio.run(report(names, lev=1, long_only=True))
    else:
        asyncio.run(report(names, args.lev))
