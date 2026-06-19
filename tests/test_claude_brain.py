"""Tests for the Claude decision brain (network-free — the `claude` CLI is mocked).

Covers: feature computation, archetype classification, the deterministic fallback
(when the CLI is unavailable), pick validation, and playbook -> signal conversion.
"""
import math
import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from core.types import Candle, MarketContext
from claude.features import compute_features
from claude import claude_brain, playbook


def _candles(closes, vols=None):
    out = []
    for i, c in enumerate(closes):
        v = vols[i] if vols else 1000.0
        out.append(Candle(ts=datetime.now(timezone.utc), open=c, high=c * 1.01,
                           low=c * 0.99, close=c, volume=v))
    return out


CTX = MarketContext(timestamp=datetime.now(timezone.utc), fear_greed_value=50,
    fear_greed_label="N", btc_dominance=55.0, total_market_cap_usd=2.5e12,
    total_market_cap_change_24h=0.0, bnb_price_usd=600.0, regime="CHOP")


def test_features_compute_and_bound():
    closes = [100.0 + 4.0 * math.sin(i / 4.0) for i in range(80)]
    f = compute_features("ETH", _candles(closes))
    assert f is not None
    assert 0.0 <= f["range_pos"] <= 1.0
    assert 0.0 <= f["reversion_score"] <= 1.0 and 0.0 <= f["breakout_score"] <= 1.0
    assert f["opportunity"] == max(f["reversion_score"], f["breakout_score"])


def test_features_none_when_short():
    assert compute_features("ETH", _candles([100.0] * 10)) is None


def test_oversold_flush_scores_reversion():
    # steady, then a sharp multi-bar flush down on rising volume -> reversion setup
    closes = [100.0] * 60 + [99, 97, 94, 90, 86, 82, 80]
    vols = [1000.0] * 60 + [1500, 2000, 2600, 3200, 4000, 5000, 6000]
    f = compute_features("ETH", _candles(closes, vols))
    assert f["rsi"] < 40 and f["reversion_score"] > f["breakout_score"]


def test_classify_known_strategies():
    assert claude_brain._classify("liq_reversion_perp")[0] == "reversion"
    assert claude_brain._classify("stochrsi_mr_perp")[0] == "reversion"
    assert claude_brain._classify("donchian_breakout")[0] == "breakout"
    assert claude_brain._classify("trend_follow")[0] == "trend"


def _watchlist():
    return {"regime": "CHOP", "scanned": 50, "ranked": [
        {"symbol": "ETH", "rsi": 22.0, "ret_4h": -4.0, "vol_spike": 3.0, "range_pos": 0.08,
         "reversion_score": 0.8, "breakout_score": 0.1, "opportunity": 0.8},
        {"symbol": "BNB", "rsi": 71.0, "ret_4h": 3.0, "vol_spike": 2.5, "range_pos": 0.9,
         "reversion_score": 0.1, "breakout_score": 0.7, "opportunity": 0.7},
    ]}


def test_fallback_picks_from_scores():
    pb = claude_brain._fallback_playbook(_watchlist(), max_picks=5, min_conv=0.5)
    assert pb["source"] == "fallback"
    syms = {p["symbol"] for p in pb["picks"]}
    assert "ETH" in syms                       # strong oversold reversion makes the cut
    for p in pb["picks"]:
        assert p["strategy"] in {m["name"] for m in claude_brain._strategy_menu()}


def test_validate_drops_unknown_strategy_and_low_conviction():
    raw = {"market_view": "x", "picks": [
        {"symbol": "ETH", "strategy": "liq_reversion_perp", "conviction": 0.8, "reason": "ok"},
        {"symbol": "BNB", "strategy": "not_a_real_strategy", "conviction": 0.9, "reason": "bad"},
        {"symbol": "XRP", "strategy": "liq_reversion_perp", "conviction": 0.2, "reason": "weak"},
    ], "avoid": ["doge"]}
    out = claude_brain._validate(raw, _watchlist(), max_picks=5, min_conv=0.55)
    names = [(p["symbol"], p["strategy"]) for p in out["picks"]]
    assert ("ETH", "liq_reversion_perp") in names
    assert all(p["strategy"] != "not_a_real_strategy" for p in out["picks"])   # unknown dropped
    assert all(p["conviction"] >= 0.55 for p in out["picks"])                  # weak dropped
    assert out["avoid"] == ["DOGE"]


@pytest.mark.anyio
async def test_decide_falls_back_when_cli_unavailable():
    with patch("claude.claude_brain._call_claude_sync", return_value=None):
        pb = await claude_brain.decide_playbook(_watchlist())
    assert pb["source"] == "fallback" and isinstance(pb["picks"], list)


@pytest.mark.anyio
async def test_decide_uses_claude_when_cli_returns_valid():
    fake = {"market_view": "buy the dip", "picks": [
        {"symbol": "ETH", "strategy": "liq_reversion_perp", "conviction": 0.8, "reason": "oversold"}],
        "avoid": []}
    with patch("claude.claude_brain._call_claude_sync", return_value=fake):
        pb = await claude_brain.decide_playbook(_watchlist())
    assert pb["source"] == "claude" and pb["picks"][0]["symbol"] == "ETH"


def test_to_signals_makes_spot_longs():
    fake_pb = {"regime": "CHOP", "picks": [
        {"symbol": "ETH", "strategy": "liq_reversion_perp", "conviction": 0.8, "reason": "oversold"}]}
    with patch("claude.playbook.get_playbook", return_value=fake_pb):
        sigs = playbook.to_signals(CTX)
    assert len(sigs) == 1
    s = sigs[0]
    assert s.direction == "long" and s.venue == "spot" and s.leverage == 1.0
    assert s.strategy_name == "claude:liq_reversion_perp" and s.symbol == "ETH"
    assert s.contract  # resolved to an on-chain contract
