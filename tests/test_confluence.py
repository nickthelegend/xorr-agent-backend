"""Confluence engine — the all-strats "is it real?" verifier (network-free)."""
from datetime import datetime, timezone, timedelta

from core.types import Candle
from claude.confluence import confluence_panel


def _candles(closes, vols=None):
    out = []
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i, c in enumerate(closes):
        out.append(Candle(ts=t0 + timedelta(hours=i), open=c, high=c * 1.005,
                          low=c * 0.995, close=c, volume=(vols[i] if vols else 1000.0)))
    return out


def test_oversold_flush_has_multi_lens_confluence():
    # steady tape, then a sharp multi-bar capitulation flush -> many reversion lenses agree
    closes = [100.0] * 60 + [98, 95, 91, 87, 83, 80]
    vols = [1000.0] * 60 + [2000, 2800, 3600, 4500, 5500, 7000]
    p = confluence_panel("TEST", _candles(closes, vols))
    assert p["side"] == "reversion"
    assert p["rev_agree"] >= 3          # RSI/StochRSI/Williams/CCI/MFI/range etc. confirm
    assert p["score"] >= 0.5


def test_flat_tape_has_no_confluence():
    closes = [100.0 + (i % 2) * 0.05 for i in range(70)]   # tiny flat noise
    p = confluence_panel("TEST", _candles(closes))
    assert p["agree"] <= 1               # nothing real here


def test_short_series_is_safe():
    p = confluence_panel("TEST", _candles([100, 101, 102]))
    assert p["agree"] == 0 and p["total"] == 10 and p["firing"] == []


def test_breakout_side_detected():
    # long quiet base then a clean thrust to new highs -> breakout lenses, not reversion
    closes = [100.0 + 0.01 * i for i in range(60)] + [101, 103, 106, 109]
    p = confluence_panel("TEST", _candles(closes))
    assert p["brk_agree"] >= 1 and p["side"] == "breakout"
