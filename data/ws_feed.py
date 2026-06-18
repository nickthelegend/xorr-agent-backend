"""Real-time price feed via Binance websocket (`!miniTicker@arr`).

One connection streams every USDT pair's last price ~once per second. We keep an
in-memory base-symbol -> price map so the RISK/EXIT monitor can mark positions on
sub-second-fresh prices instead of the ~8s REST cache (a fast gap can jump a stop
between REST polls; the WS catches it). Pure overlay: REST stays the source of
truth for everything except the live mark, and the feed fails open (if the socket
drops we fall back to REST and auto-reconnect).
"""
import asyncio
import json
import logging
import time

logger = logging.getLogger("xorr.data.ws_feed")

_WS_URL = "wss://stream.binance.com:9443/ws/!miniTicker@arr"

_prices: dict = {}        # base symbol (e.g. "BTC") -> last price
_last: dict = {}          # base symbol -> monotonic timestamp of last update
_task = None
_started = False
_connected = False
_last_msg_ts = 0.0


def get_price(base_symbol: str, max_age_sec: float = 10.0):
    """Latest WS price for a base symbol (e.g. 'ETH'), or None if absent/stale."""
    sym = (base_symbol or "").upper()
    p = _prices.get(sym)
    if p is None or p <= 0:
        return None
    if (time.monotonic() - _last.get(sym, 0.0)) > max_age_sec:
        return None
    return p


def overlay(quotes: dict) -> None:
    """Overwrite each Quote.price with the fresher WS mark when available."""
    for sym, quote in quotes.items():
        wp = get_price(sym)
        if wp is not None:
            try:
                quote.price = wp
            except Exception:
                pass


def status() -> dict:
    age = round(time.monotonic() - _last_msg_ts, 1) if _last_msg_ts else None
    return {"started": _started, "connected": _connected, "symbols": len(_prices), "last_msg_age_sec": age}


def ensure_started() -> None:
    """Idempotently start the feed task on the current running loop."""
    global _task, _started
    if _started:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop yet; will start on the next async call
    _started = True
    _task = loop.create_task(_feed_loop())
    logger.info("[WS] price feed task started")


async def _feed_loop() -> None:
    global _connected, _last_msg_ts
    try:
        import websockets
    except Exception as e:
        logger.warning(f"[WS] websockets lib unavailable ({e}); staying on REST")
        return
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(_WS_URL, ping_interval=20, ping_timeout=20, max_size=2 ** 22) as ws:
                _connected = True
                backoff = 1.0
                logger.info("[WS] connected to Binance miniTicker stream")
                async for msg in ws:
                    _last_msg_ts = time.monotonic()
                    try:
                        arr = json.loads(msg)
                    except Exception:
                        continue
                    if not isinstance(arr, list):
                        arr = [arr]
                    now = time.monotonic()
                    for t in arr:
                        s = t.get("s") or ""
                        if not s.endswith("USDT"):
                            continue
                        try:
                            price = float(t.get("c") or 0.0)
                        except (TypeError, ValueError):
                            continue
                        if price > 0:
                            base = s[:-4]
                            _prices[base] = price
                            _last[base] = now
        except asyncio.CancelledError:
            break
        except Exception as e:
            _connected = False
            logger.warning(f"[WS] feed disconnected ({e}); reconnecting in {backoff:.0f}s")
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                break
            backoff = min(30.0, backoff * 2.0)
    _connected = False
