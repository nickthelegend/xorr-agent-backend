"""Live-execution tests — they DON'T touch a real chain (no funds are moved), but
they exercise the exact LIVE code path by mocking the `twak` subprocess: they
assert the executor builds the correct CLI commands and parses TWAK's JSON the
right way, for spot swaps, perp open/close/mark, registration, and the
eligibility guard. This is how we test the live trade plumbing without a wallet.

Run: python -m pytest tests/test_live_execution.py -q
"""
import asyncio
from decimal import Decimal

from config import settings
from core.twak_executor import TwakExecutor
from data.tokens import resolve

USDT = settings.usdt_contract
ETH = resolve("ETH").contract


def _live_executor():
    """A live-mode executor with the twak subprocess mocked. Records every CLI
    arg list it would have run, and returns canned TWAK JSON per command."""
    ex = TwakExecutor(simulation=False)
    ex._twak_ready = lambda: True          # pretend CLI + creds are present
    ex._cached_address = "0xAGENT"
    calls = []

    async def fake_run(args, timeout=30):
        calls.append(list(args))
        head = args[:2]
        if head == ["perps", "open"]:
            return {"txHash": "0xperpopen", "entryPrice": 1750.0, "size": 0.012}
        if head == ["perps", "close"]:
            return {"txHash": "0xperpclose", "closePrice": 1700.0}
        if head == ["perps", "mark"]:
            return {"markPrice": 1748.0}
        if head == ["compete", "register"]:
            return {"txHash": "0xreg"}
        if args and args[0] == "swap":
            return {"txHash": "0xswap", "output": 0.0028}
        return {}

    ex._run_twak = fake_run
    return ex, calls


def test_live_spot_swap_command_and_parse():
    ex, calls = _live_executor()
    res = asyncio.run(ex.swap(USDT, ETH, Decimal("5"), Decimal("0.0027"),
                              reason="ENTRY_donchian_breakout", ref_price=1750.0))
    assert len(calls) == 1
    cmd = calls[0]
    # verified form: twak swap <AMOUNT> <FROM> <TO> --chain bsc --slippage <pct>
    assert cmd[0] == "swap" and cmd[1] == "5" and cmd[2] == USDT and cmd[3] == ETH
    assert "--chain" in cmd and cmd[cmd.index("--chain") + 1] == "bsc"
    assert "--slippage" in cmd
    assert "--password" not in cmd          # secrets go via env, never argv
    assert res.success and res.tx_hash == "0xswap"


def test_live_perp_open_long_command():
    ex, calls = _live_executor()
    res = asyncio.run(ex.open_perp("ETH", "long", Decimal("7"), 3.0, ref_price=1750.0))
    assert calls[0] == ["perps", "open", "ETH", "--side", "long",
                        "--usd", "7.00", "--leverage", "3.0", "--chain", "bsc"]
    assert res.success and res.executed_price == 1750.0 and abs(res.amount_out - 0.012) < 1e-9


def test_live_perp_open_short_command():
    ex, calls = _live_executor()
    asyncio.run(ex.open_perp("ETH", "short", Decimal("6"), 3.0, ref_price=1750.0))
    assert calls[0][:5] == ["perps", "open", "ETH", "--side", "short"]


def test_perp_usd_is_notional_toggle():
    ex, calls = _live_executor()
    old = settings.perp_usd_is_margin
    try:
        settings.perp_usd_is_margin = False   # this build reads --usd as NOTIONAL
        asyncio.run(ex.open_perp("ETH", "long", Decimal("7"), 3.0, ref_price=1750.0))
        # 7 margin * 3x = 21.00 notional passed as --usd
        assert calls[0][calls[0].index("--usd") + 1] == "21.00"
    finally:
        settings.perp_usd_is_margin = old


def test_live_perp_close_pnl_parse():
    ex, calls = _live_executor()
    # long 0.012 units, entry 1750, margin 7; close at 1700 -> uPnL = 0.012*(1700-1750) = -0.6
    res = asyncio.run(ex.close_perp("ETH", "long", 0.012, 1750.0, 7.0, 3.0, ref_price=1700.0))
    assert calls[0] == ["perps", "close", "ETH", "--chain", "bsc"]
    assert res.success and abs(res.amount_out - 6.4) < 1e-6   # 7 + (-0.6)


def test_live_perp_mark():
    ex, calls = _live_executor()
    mark = asyncio.run(ex.perp_mark("ETH"))
    assert calls[0] == ["perps", "mark", "ETH", "--chain", "bsc"]
    assert mark == 1748.0


def test_live_register():
    ex, calls = _live_executor()
    tx = asyncio.run(ex.register_for_competition())
    assert calls[0][:2] == ["compete", "register"]
    assert tx == "0xreg"


def test_live_eligibility_guard_blocks_non_whitelisted():
    ex, calls = _live_executor()
    bogus = "0x000000000000000000000000000000000000dEaD"
    res = asyncio.run(ex.swap(USDT, bogus, Decimal("5"), Decimal("0"),
                              reason="ENTRY_x", ref_price=1.0))
    assert not res.success
    assert "guardrail" in (res.error or "").lower()
    assert calls == []   # rejected before any CLI call


def test_perps_unavailable_without_creds_fails_clean():
    ex = TwakExecutor(simulation=False)
    ex._twak_ready = lambda: False   # no CLI/creds -> perps must fail clearly, not crash
    res = asyncio.run(ex.open_perp("ETH", "long", Decimal("7"), 3.0, ref_price=1750.0))
    assert not res.success and "TWAK" in (res.error or "")
