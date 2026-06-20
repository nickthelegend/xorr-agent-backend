"""TWAK output parsing — the CLI returns amounts as symbol-suffixed strings ('3.62 CAKE'),
not raw numbers. Regression guard for the live-swap parse bug."""
from core.twak_executor import _num_amount


def test_parses_symbol_suffixed_amount():
    assert abs(_num_amount("3.620155 CAKE") - 3.620155) < 1e-9
    assert abs(_num_amount("5.0 USDT") - 5.0) < 1e-9
    assert abs(_num_amount("0.008547393208387839 BNB") - 0.008547393208387839) < 1e-12


def test_parses_plain_and_comma():
    assert _num_amount("42") == 42.0
    assert _num_amount("1,234.5 USDT") == 1234.5


def test_safe_on_garbage():
    assert _num_amount(None) == 0.0
    assert _num_amount("") == 0.0
    assert _num_amount("CAKE") == 0.0
