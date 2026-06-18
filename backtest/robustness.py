"""4-way robustness gauntlet — the honest test of whether a strategy is real.

For every strategy, on 1h data with realistic costs:
  OOS    — train/reference on the FIRST half of BTC, score on the UNSEEN second half
  Sens   — perturb the strategy's primary parameter by +/-15% and +/-30%; count how
           many of {-30,-15,base,+15,+30} stay positive-expectancy (x/5)
  comm2x — double the round-trip commission (realistic-cost stress)
  Multi  — same params on ETH and SOL (cross-asset generalization)

A strategy "SURVIVES" only if OOS expectancy > 0 AND Sens >= 3/5 AND comm2x
expectancy > 0 AND at least one of ETH/SOL is positive. Strategies are ranked by
OOS expectancy (performance on data they were never seen on).

Run:  python -X utf8 -m backtest.robustness            (default set)
      python -X utf8 -m backtest.robustness --all       (every perp strategy)
"""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional

from core.types import Candle, MarketContext
from backtest.data_loader import get_klines_for_backtest
from strategies.registry import STRATEGIES
from config import settings

DAYS = 200
LEVERAGE = 3.0
BASE_FEE = 0.0006          # per side (12 bps round-trip)
MARGIN = 12.0             # fixed fraction of the 100 starting bank
MAX_CONC = 2

# strategy -> (settings attribute, baseline value) for the Sens sweep
PRIMARY_PARAM = {
    "donchian_perp": ("donchian_channel_bars", 55),
    "salamander_perp": ("salamander_pullback_max", 7.0),
    "supertrend_perp": ("supertrend_period", 10),
    "adaptive_percentile_reversion_perp": ("adaptive_percentile", 95.0),
    "adaptive_percentile_momentum_perp": ("adaptive_percentile", 95.0),
    "cascade_filter_perp": ("adaptive_percentile", 95.0),
    "volume_confirmed_reversion_perp": ("adaptive_percentile", 95.0),
    "burst_scalper_perp": ("adaptive_percentile", 95.0),
    "cascade_consec_perp": ("consec_bars", 4),
    "dominant_burst_perp": ("burst_imbalance_ratio", 5.0),
    "adaptive_p99_momentum_perp": ("adaptive_percentile", 95.0),
    "tsi_mr_perp": ("tsi_entry_thresh", 25.0),
    "uo_mr_perp": ("uo_oversold", 35.0),
    "aroon_mr_perp": ("aroon_entry_thresh", 50.0),
}
DEFAULT_PARAM = ("liq_z_threshold", 2.5)   # most strategies are liq-cascade gated


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

    def regime_at(ts_ms: int) -> str:
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


async def simulate(strat, candles: List[Candle], regime_at, symbol: str,
                   leverage: float, fee: float, lo: int, hi: int) -> Dict:
    cash = 100.0; peak = 100.0; maxdd = 0.0
    open_pos: List[dict] = []
    trades: List[float] = []
    ff = float(settings.perp_funding_rate_8h)
    start = max(125, lo)
    for i in range(start, hi):
        bar = candles[i]
        ts_ms = int(bar.ts.timestamp() * 1000)
        regime = regime_at(ts_ms)
        # exits
        still = []
        for p in open_pos:
            hold = i - p["i"]
            fav = ((bar.close - p["entry"]) / p["entry"]) * (1 if p["dir"] == "long" else -1) * 100.0
            p["peak"] = max(p["peak"], bar.high); p["trough"] = min(p["trough"], bar.low)
            ex = None
            if hold >= p["mh"]:
                ex = bar.close
            elif not p["tp1"] and ((p["dir"] == "long" and bar.low <= p["stop"]) or (p["dir"] == "short" and bar.high >= p["stop"])):
                ex = p["stop"]
            elif not p["tp1"] and ((p["dir"] == "long" and bar.high >= p["tp"]) or (p["dir"] == "short" and bar.low <= p["tp"])):
                ex = p["tp"]
            else:
                if fav >= 2.5 and not p["tp1"]:
                    p["tp1"] = True
                    p["stop"] = max(p["stop"], p["entry"] * 1.005, p["peak"] * 0.97) if p["dir"] == "long" \
                        else min(p["stop"], p["entry"] * 0.995, p["trough"] * 1.03)
                elif p["tp1"]:
                    if p["dir"] == "long":
                        p["stop"] = max(p["stop"], p["peak"] * 0.97)
                        if bar.low <= p["stop"]:
                            ex = p["stop"]
                    else:
                        p["stop"] = min(p["stop"], p["trough"] * 1.03)
                        if bar.high >= p["stop"]:
                            ex = p["stop"]
            if ex is None:
                still.append(p); continue
            gross = p["size"] * (ex - p["entry"]) * (1 if p["dir"] == "long" else -1)
            cost = (p["size"] * p["entry"] + p["size"] * ex) * fee
            funding = (p["size"] * ex) * ff * (hold / 8.0)
            pnl = gross - cost - funding
            cash += p["margin"] + pnl
            risk = p["size"] * abs(p["entry"] - p["init"])
            trades.append(pnl / risk if risk > 0 else 0.0)
        open_pos = still
        # entry
        if len(open_pos) < MAX_CONC:
            hist = candles[max(0, i - 120):i + 1]
            ctx = MarketContext(timestamp=bar.ts, fear_greed_value=50, fear_greed_label="N",
                                btc_dominance=55, total_market_cap_usd=2.5e12,
                                total_market_cap_change_24h=0, bnb_price_usd=600, regime=regime)
            try:
                sig = await strat.evaluate(symbol, [], hist, ctx)
            except Exception:
                sig = None
            if sig and not any(pp for pp in open_pos):
                entry = bar.close
                if sig.direction == "short":
                    stop = entry * (1 + sig.stop_loss_pct / 100.0); tp = entry * (1 - sig.take_profit_pct / 100.0)
                else:
                    stop = entry * (1 - sig.stop_loss_pct / 100.0); tp = entry * (1 + sig.take_profit_pct / 100.0)
                size = MARGIN * leverage / entry
                cash -= (MARGIN + size * entry * fee)
                open_pos.append({"entry": entry, "dir": sig.direction, "stop": stop, "tp": tp,
                                 "init": stop, "size": size, "margin": MARGIN, "i": i, "tp1": False,
                                 "peak": entry, "trough": entry, "mh": max(1, int(sig.max_hold_min / 60))})
        # equity / drawdown
        eq = cash
        for p in open_pos:
            up = p["size"] * (bar.close - p["entry"]) * (1 if p["dir"] == "long" else -1)
            eq += max(0.0, p["margin"] + up)
        peak = max(peak, eq)
        if peak > 0:
            maxdd = max(maxdd, (peak - eq) / peak * 100.0)
    # close leftovers
    last = candles[hi - 1]
    for p in open_pos:
        gross = p["size"] * (last.close - p["entry"]) * (1 if p["dir"] == "long" else -1)
        cost = (p["size"] * p["entry"] + p["size"] * last.close) * fee
        cash += p["margin"] + gross - cost
        risk = p["size"] * abs(p["entry"] - p["init"])
        trades.append((gross - cost) / risk if risk > 0 else 0.0)
    n = len(trades)
    wins = sum(1 for r in trades if r > 0)
    return {"n": n, "win": (wins / n * 100.0) if n else 0.0,
            "exp": (sum(trades) / n) if n else 0.0, "ret": cash - 100.0, "maxdd": maxdd}


class override:
    """Temporarily set a settings attribute (restored on exit)."""
    def __init__(self, **kw): self.kw = kw; self.old = {}
    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(settings, k, None); setattr(settings, k, v)
    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(settings, k, v)


async def gauntlet(name: str, cls, data: Dict[str, List[Candle]], regime_at) -> Dict:
    btc = data["BTC"]; n_btc = len(btc); half = n_btc // 2
    attr, base = PRIMARY_PARAM.get(name, DEFAULT_PARAM)

    async def run_on(symbol, lo, hi, fee=BASE_FEE):
        with override(perp_symbols=symbol):   # let the strategy accept this symbol
            return await simulate(cls(), data[symbol], regime_at, symbol, LEVERAGE, fee, lo, hi)

    base_res = await run_on("BTC", 0, n_btc)
    oos = await run_on("BTC", half, n_btc)              # unseen second half
    comm2x = await run_on("BTC", 0, n_btc, fee=BASE_FEE * 2)
    eth = await run_on("ETH", 0, len(data["ETH"]))
    sol = await run_on("SOL", 0, len(data["SOL"]))

    # Sens: perturb the primary parameter
    sens_pos = 1 if base_res["exp"] > 0 else 0   # baseline counts
    for mult in (0.85, 1.15, 0.70, 1.30):
        val = type(base)(base * mult)
        with override(perp_symbols="BTC", **{attr: val}):   # must also accept BTC
            r = await simulate(cls(), btc, regime_at, "BTC", LEVERAGE, BASE_FEE, 0, n_btc)
        if r["exp"] > 0:
            sens_pos += 1

    survives = (oos["exp"] > 0 and sens_pos >= 3 and comm2x["exp"] > 0 and (eth["exp"] > 0 or sol["exp"] > 0))
    return {"name": name, "base": base_res, "oos": oos, "comm2x": comm2x,
            "eth": eth, "sol": sol, "sens": sens_pos, "survives": survives}


DEFAULT_SET = [
    "liq_reversion_perp", "liq_support_reversion_perp", "liq_climax_reversion_perp",
    "liq_squeeze_break_perp", "salamander_perp", "donchian_perp",
    "adaptive_percentile_reversion_perp", "adaptive_percentile_momentum_perp",
    "cascade_filter_perp", "volume_confirmed_reversion_perp", "burst_scalper_perp",
    "liq_zscore_perp", "liq_divergence_fade_perp", "supertrend_perp",
    "tsi_mr_perp", "uo_mr_perp", "aroon_mr_perp",
]


async def main(strat_names: List[str]):
    print(f"Loading ~{DAYS}d 1h data for BTC/ETH/SOL ...")
    data = {}
    for s in ("BTC", "ETH", "SOL"):
        df = await get_klines_for_backtest(s, "1h", days=DAYS)
        data[s] = candles_from(df) if df is not None and not df.empty else []
    btc_1d = await get_klines_for_backtest("BTC", "1d", days=DAYS + 5)
    regime_at = make_regime_fn(btc_1d)
    if not data["BTC"] or not data["ETH"] or not data["SOL"]:
        print("Missing data; aborting."); return

    n = min(len(data["BTC"]), len(data["ETH"]), len(data["SOL"]))
    print(f"BTC bars: {len(data['BTC'])}  ETH: {len(data['ETH'])}  SOL: {len(data['SOL'])}  (OOS split at BTC/2)\n")

    rows = []
    for name in strat_names:
        cls = STRATEGIES.get(name)
        if not cls:
            continue
        rows.append(await gauntlet(name, cls, data, regime_at))

    rows.sort(key=lambda r: (r["survives"], r["oos"]["exp"]), reverse=True)
    print("=" * 118)
    print(f"{'strategy':34s} {'baseExp':>8} {'OOSexp':>8} {'OOSret%':>8} {'maxDD%':>7} {'Sens':>5} {'comm2x':>8} {'ETHexp':>8} {'SOLexp':>8}  SURVIVES")
    print("-" * 118)
    for r in rows:
        print(f"{r['name']:34s} {r['base']['exp']:+8.3f} {r['oos']['exp']:+8.3f} {r['oos']['ret']:+8.1f} "
              f"{r['oos']['maxdd']:7.1f} {r['sens']:>3}/5 {r['comm2x']['exp']:+8.3f} "
              f"{r['eth']['exp']:+8.3f} {r['sol']['exp']:+8.3f}  {'YES' if r['survives'] else 'no'}")
    print("=" * 118)
    surv = [r["name"] for r in rows if r["survives"]]
    print(f"SURVIVORS ({len(surv)}): {', '.join(surv) if surv else 'none'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="every perp strategy")
    args = ap.parse_args()
    names = [k for k in STRATEGIES if "perp" in k] if args.all else DEFAULT_SET
    asyncio.run(main(names))
