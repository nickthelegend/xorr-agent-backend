"""Telegram notifications — open/close trade alerts with win/loss, sent to the owner.

Fire-and-forget: every send is wrapped so a Telegram outage never affects trading.
Configure TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env (gitignored). Uses HTML parse
mode (underscores in strategy names are safe in HTML, unlike Markdown).
"""
import asyncio
import os
from typing import Optional

import httpx

from config import settings


def _token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", getattr(settings, "telegram_bot_token", "") or "")


def _chat() -> str:
    return os.environ.get("TELEGRAM_CHAT_ID", getattr(settings, "telegram_chat_id", "") or "")


def _proxy() -> Optional[str]:
    # Needed where api.telegram.org is ISP-blocked (e.g. India). Set TELEGRAM_PROXY to an
    # http(s):// or socks5:// proxy reachable from this machine. Unset on a VPS where
    # Telegram is reachable directly.
    return os.environ.get("TELEGRAM_PROXY", getattr(settings, "telegram_proxy", "") or "") or None


def enabled() -> bool:
    return bool(_token() and _chat())


def fire(coro) -> None:
    """Schedule a notification WITHOUT blocking the caller (fire-and-forget), so a slow or
    blocked Telegram never stalls the trading loop. No-op if there's no running loop."""
    try:
        asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        pass


async def send(text: str) -> bool:
    """Send a message to the owner chat. Never raises. Short timeout so a blocked
    endpoint fails fast rather than hanging the caller."""
    if not enabled():
        return False
    try:
        kw = {"timeout": 8.0}
        proxy = _proxy()
        if proxy:
            kw["proxy"] = proxy
        async with httpx.AsyncClient(**kw) as c:
            r = await c.post(
                f"https://api.telegram.org/bot{_token()}/sendMessage",
                json={"chat_id": _chat(), "text": text, "parse_mode": "HTML",
                      "disable_web_page_preview": True},
            )
            if r.status_code != 200:
                print(f"[telegram] send non-200: {r.status_code} {r.text[:120]}")
            return r.status_code == 200
    except Exception as e:
        print(f"[telegram] send failed (blocked? set TELEGRAM_PROXY): {str(e)[:80]}")
        return False


def _clean_strat(name: str) -> str:
    return str(name or "").replace("claude:", "").replace("_perp", "").replace("_", " ")


_EXIT_WORDS = {
    "SL_HIT": "🛑 stop loss",
    "TP_HIT": "🏆 take profit",
    "TRAIL_STOP_PROFIT": "📈 trailing stop (profit locked)",
    "BREAKEVEN_STOP": "🟰 breakeven stop (risk-free)",
    "MAX_HOLD_TIME": "⏰ time stop",
    "STAGNATION_EXIT": "💤 stagnation exit",
    "LIQ_GUARD": "🛟 liquidation guard",
}


def _humanize_exit(reason: str) -> str:
    return _EXIT_WORDS.get(str(reason or "").upper(), str(reason or "exit"))


def _fmt_usd(x: float) -> str:
    ax = abs(x)
    if ax >= 1:
        return f"${x:,.2f}"
    if ax >= 0.01:
        return f"${x:.4f}"
    return f"${x:.8f}".rstrip("0")


async def notify_open(symbol: str, strategy: str, size_usd: float, entry: float,
                      stop: float, target: float, conviction: float,
                      equity: float, open_n: int, max_n: int, mode: str = "sim") -> None:
    sl_pct = ((stop - entry) / entry * 100.0) if entry else 0.0
    tp_pct = ((target - entry) / entry * 100.0) if entry else 0.0
    tag = "" if mode == "live" else " <i>(paper)</i>"
    msg = (
        f"🟢 <b>OPENED</b> · <b>{symbol}</b>{tag}\n"
        f"🎯 Entry: {_fmt_usd(entry)}\n"
        f"💵 Size: {_fmt_usd(size_usd)}\n"
        f"🛑 Stop: {_fmt_usd(stop)} ({sl_pct:+.1f}%)\n"
        f"🏆 Target: {_fmt_usd(target)} ({tp_pct:+.1f}%)\n"
        f"🧠 {_clean_strat(strategy)} · conviction {conviction*100:.0f}%\n"
        f"💼 Equity {_fmt_usd(equity)} · {open_n}/{max_n} open"
    )
    await send(msg)


async def notify_close(symbol: str, strategy: str, pnl_usd: float, pnl_pct: float,
                       r_mult: float, hold_min: float, exit_reason: str,
                       equity: Optional[float] = None, mode: str = "sim") -> None:
    won = pnl_usd > 0
    head = "✅ <b>WON</b>" if won else "🔴 <b>LOST</b>"
    money = "📈" if won else "📉"
    hold_h = hold_min / 60.0
    hold_str = f"{hold_h:.1f}h" if hold_h >= 1 else f"{hold_min:.0f}m"
    tag = "" if mode == "live" else " <i>(paper)</i>"
    lines = [
        f"{head} · <b>{symbol}</b>  {'+' if won else '−'}{_fmt_usd(abs(pnl_usd))}{tag}",
        f"{money} {pnl_pct:+.1f}% · {r_mult:+.2f}R",
        f"⏱️ Held {hold_str}",
        f"🚪 {_humanize_exit(exit_reason)}",
        f"🧠 {_clean_strat(strategy)}",
    ]
    if equity is not None:
        lines.append(f"💼 Equity {_fmt_usd(equity)}")
    await send("\n".join(lines))


async def notify_startup(mode: str, interval_hours: float) -> None:
    await send(
        f"🤖 <b>XORR online</b>\n"
        f"⚙️ Mode: <b>{mode.upper()}</b>\n"
        f"🔍 Hunting new setups every {interval_hours:g}h · watching live every ~1-3 min\n"
        f"📡 Trade alerts will appear here."
    )
