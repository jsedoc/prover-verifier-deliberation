"""Unit tests for tables/table9_rttr_stats.py CI formatting."""

from tables.table9_rttr_stats import _ci


def test_ci_unsigned():
    assert _ci(74.2, 67.7, 79.8) == "74.2 [67.7, 79.8]"

def test_ci_positive_mid_signed():
    assert _ci(4.0, -17.7, 29.1, signed=True) == "+4.0 [-17.7, +29.1]"

def test_ci_negative_mid_signed():
    # regression: a negative signed mid must render '-4.5', never '+-4.5'
    assert _ci(-4.5, -20.0, 10.0, signed=True) == "-4.5 [-20.0, +10.0]"

def test_ci_none_mid():
    assert _ci(None, 0.0, 10.0) == "\\textemdash"

def test_ci_rounds_to_one_decimal():
    assert _ci(42.407, 31.04, 53.63, signed=True) == "+42.4 [+31.0, +53.6]"
