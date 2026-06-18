"""Real-time liquidation feed via Binance Futures `!forceOrder@arr` websocket.

This is the data layer the liquidation-flow strategies (moon-dev / piranha class)
need and which klines do NOT contain: the forced-liquidation tape. Each event is a
position being force-closed —
    side SELL  => a LONG was liquidated  => forced selling  => DOWNWARD flush
    side BUY   => a SHORT was liquidated => forced buying    => UPWARD flush
We keep a per-base-symbol rolling history and derive the cascade metrics the
strategies trade on (net flow, z-score vs a rolling regime, relative spike,
imbalance). Live-only and accumulating: the z-score needs warm-up history, so the
strategies that use it are shadow-tested until the regime baseline fills in.
Fail-open + auto-reconnect, identical lifecycle to data/ws_feed.py.
"""
import asyncio
import json
import logging
import time
from collections import defaultdict, deque

logger = logging.getLogger("xorr.data.liq_feed")

_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"
_HISTORY_SEC = 4 * 3600.0      # keep 4h of events for the rolling regime
_BIN_SEC = 300.0               # 5-min bins
_RECENT_SEC = 300.0            # "current" cascade window

# base symbol -> deque[(monotonic_ts, side_sign, usd)]  (side_sign: +1 up / -1 down)
_events: dict = defaultdict(lambda: deque())
_task = None
_started = False
_connected = False
_last_msg_ts = 0.0


def _prune(dq: deque, now: float) -> None:
    cutoff = now - _HISTORY_SEC
    while dq and dq[0][0] < cutoff:
        dq.popleft()


def liq_metrics(base_symbol: str):
    """Cascade metrics for a base symbol (e.g. 'BTC'), or None if not enough data.
    Returns: total_usd (recent window), net_usd (short-long; +ve = upward squeeze),
    imbalance (-1..1; +ve = longs liquidated harder), zscore + rel_spike of the
    recent window vs the 4h regime, and flush_dir ('down'/'up')."""
    sym = (base_symbol or "").upper()
    dq = _events.get(sym)
    if not dq:
        return None
    now = time.monotonic()
    _prune(dq, now)
    if len(dq) < 5:
        return None

    # recent window
    rec_long = rec_short = 0.0
    for ts, sign, usd in reversed(dq):
        if now - ts > _RECENT_SEC:
            break
        if sign < 0:
            rec_long += usd      # long liquidated (sell)
        else:
            rec_short += usd     # short liquidated (buy)
    recent_total = rec_long + rec_short

    # bin the 4h history for the regime baseline
    bins = defaultdict(float)
    for ts, sign, usd in dq:
        b = int((now - ts) // _BIN_SEC)
        bins[b] += usd
    prior = [v for k, v in bins.items() if k >= 1]  # exclude the current bin (k==0)
    if len(prior) < 6:
        return None
    mean = sum(prior) / len(prior)
    var = sum((x - mean) ** 2 for x in prior) / len(prior)
    std = var ** 0.5
    z = (recent_total - mean) / std if std > 0 else 0.0
    rel_spike = recent_total / mean if mean > 0 else 0.0

    net = rec_short - rec_long
    imbalance = (rec_long - rec_short) / recent_total if recent_total > 0 else 0.0
    return {
        "symbol": sym, "total_usd": recent_total, "net_usd": net,
        "imbalance": imbalance, "zscore": z, "rel_spike": rel_spike,
        "flush_dir": "down" if net < 0 else "up",
    }


def status() -> dict:
    age = round(time.monotonic() - _last_msg_ts, 1) if _last_msg_ts else None
    return {"started": _started, "connected": _connected,
            "symbols_tracked": len([k for k, v in _events.items() if v]),
            "last_event_age_sec": age}


def ensure_started() -> None:
    global _task, _started
    if _started:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _started = True
    _task = loop.create_task(_feed_loop())
    logger.info("[LIQ] liquidation feed task started")


async def _feed_loop() -> None:
    global _connected, _last_msg_ts
    try:
        import websockets
    except Exception as e:
        logger.warning(f"[LIQ] websockets lib unavailable ({e})")
        return
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(_URL, ping_interval=20, ping_timeout=20, max_size=2 ** 22) as ws:
                _connected = True
                backoff = 1.0
                logger.info("[LIQ] connected to Binance forceOrder stream")
                async for msg in ws:
                    _last_msg_ts = time.monotonic()
                    try:
                        evt = json.loads(msg)
                    except Exception:
                        continue
                    o = evt.get("o") or {}
                    s = o.get("s") or ""
                    if not s.endswith("USDT"):
                        continue
                    try:
                        qty = float(o.get("q") or 0.0)
                        price = float(o.get("ap") or o.get("p") or 0.0)
                    except (TypeError, ValueError):
                        continue
                    usd = qty * price
                    if usd <= 0:
                        continue
                    # SELL = long liquidated = downward flush (-1); BUY = short liq = up (+1)
                    sign = -1 if str(o.get("S")).upper() == "SELL" else 1
                    base = s[:-4]
                    _events[base].append((time.monotonic(), sign, usd))
        except asyncio.CancelledError:
            break
        except Exception as e:
            _connected = False
            logger.warning(f"[LIQ] feed disconnected ({e}); reconnecting in {backoff:.0f}s")
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                break
            backoff = min(30.0, backoff * 2.0)
    _connected = False
