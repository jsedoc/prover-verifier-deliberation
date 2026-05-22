"""Unit tests for rttr.stats — statistical helpers."""

import math
import pytest

from rttr.stats import (
    bootstrap_ci,
    gap_fisher_p,
    mcnemar_p,
    wilson_ci,
    _gap_stat,
)


# --------------------------------------------------------------------------- #
# wilson_ci
# --------------------------------------------------------------------------- #

def test_wilson_ci_all_correct():
    lo, mid, hi = wilson_ci(100, 100)
    assert mid == pytest.approx(100.0)
    assert hi == pytest.approx(100.0)
    assert lo < 100.0  # interval is one-sided at boundary

def test_wilson_ci_none_correct():
    lo, mid, hi = wilson_ci(0, 100)
    assert mid == pytest.approx(0.0)
    assert lo == pytest.approx(0.0)
    assert hi > 0.0

def test_wilson_ci_zero_n():
    lo, mid, hi = wilson_ci(0, 0)
    assert all(math.isnan(v) for v in (lo, mid, hi))

def test_wilson_ci_bounds_in_range():
    lo, mid, hi = wilson_ci(50, 198)
    assert 0.0 <= lo <= mid <= hi <= 100.0

def test_wilson_ci_mid_is_sample_proportion():
    lo, mid, hi = wilson_ci(60, 198)
    assert mid == pytest.approx(100 * 60 / 198)


# --------------------------------------------------------------------------- #
# mcnemar_p
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# gap_fisher_p
# --------------------------------------------------------------------------- #

def test_gap_fisher_p_in_unit_interval():
    p = gap_fisher_p(50, 100, 40, 100)
    assert 0.0 < p < 1.0

def test_gap_fisher_p_large_separation_is_tiny():
    # HC near-perfect, non-HC near-chance → strongly significant
    p = gap_fisher_p(95, 100, 50, 100)
    assert p < 0.001

def test_gap_fisher_p_empty_group_is_nan():
    assert math.isnan(gap_fisher_p(10, 10, 0, 0))
    assert math.isnan(gap_fisher_p(0, 0, 5, 10))

def test_gap_fisher_p_degenerate_no_variation_is_one():
    # both groups all-correct (or all-wrong): no outcome variation → p == 1.0
    assert gap_fisher_p(100, 100, 100, 100) == pytest.approx(1.0)
    assert gap_fisher_p(0, 100, 0, 100) == pytest.approx(1.0)

def test_gap_fisher_p_returns_python_float():
    assert type(gap_fisher_p(50, 100, 40, 100)) is float


def test_mcnemar_zero_discordant():
    # Identical vectors → no discordant pairs → p = 1.0
    a = [True, True, False, False]
    assert mcnemar_p(a, a) == pytest.approx(1.0)

def test_mcnemar_large_discordance():
    # All discordant pairs go in one direction: a always correct, b never correct
    # b_count=100, c_count=0 → chi2 = (99)^2/100 = 98.01 → p << 0.001
    a = [True] * 100
    b = [False] * 100
    p = mcnemar_p(a, b)
    assert p < 0.001

def test_mcnemar_symmetric():
    # swapping a and b gives same p (McNemar is symmetric)
    a = [True, True, False, False, True]
    b = [False, True, True, False, False]
    assert mcnemar_p(a, b) == pytest.approx(mcnemar_p(b, a))


# --------------------------------------------------------------------------- #
# bootstrap_ci
# --------------------------------------------------------------------------- #

def _make_records(n_hc_correct, n_hc_wrong, n_nhc_correct, n_nhc_wrong):
    """Build minimal synthetic pvd records. HC = outcome accept_no_change."""
    records = []
    for _ in range(n_hc_correct):
        records.append({"correct": True,  "outcome": "accept_no_change", "schema_version": "rttr-v1"})
    for _ in range(n_hc_wrong):
        records.append({"correct": False, "outcome": "accept_no_change", "schema_version": "rttr-v1"})
    for _ in range(n_nhc_correct):
        records.append({"correct": True,  "outcome": "reject",           "schema_version": "rttr-v1"})
    for _ in range(n_nhc_wrong):
        records.append({"correct": False, "outcome": "reject",           "schema_version": "rttr-v1"})
    return records


def test_bootstrap_ci_deterministic():
    records = _make_records(60, 20, 30, 88)  # HC: 75%, non-HC: 25%
    ci1 = bootstrap_ci(records, "pvd", n_boot=500, seed=0)
    ci2 = bootstrap_ci(records, "pvd", n_boot=500, seed=0)
    assert ci1 == ci2

def test_bootstrap_ci_contains_observed():
    records = _make_records(60, 20, 30, 88)
    lo, obs, hi = bootstrap_ci(records, "pvd", n_boot=1000, seed=42)
    assert lo <= obs <= hi

def test_bootstrap_ci_empty_partition_returns_nan():
    # All HC → no non-HC → gap is NaN → returns (nan, nan, nan)
    records = _make_records(80, 20, 0, 0)
    lo, obs, hi = bootstrap_ci(records, "pvd", n_boot=100, seed=42)
    assert math.isnan(obs)

def test_mcnemar_p_returns_python_float():
    # scipy returns numpy.float64 internally; we must unwrap to plain float
    a = [True, True, False]
    b = [False, True, False]
    result = mcnemar_p(a, b)
    assert type(result) is float

def test_bootstrap_ci_extreme_minority():
    # 5 HC, 195 non-HC (2.5% minority) — verify degenerate exclusion is rare
    records = _make_records(4, 1, 80, 115)  # 5 HC, 195 non-HC
    lo, obs, hi = bootstrap_ci(records, "pvd", n_boot=2000, seed=7)
    assert math.isfinite(lo) and math.isfinite(hi)
    assert lo <= obs <= hi
