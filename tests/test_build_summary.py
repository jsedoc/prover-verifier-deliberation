"""Unit tests for tables/build_summary.py efficiency resolution."""

import pytest

from tables.build_summary import _logged_eff, _eff


# --------------------------------------------------------------------------- #
# _logged_eff — schema handling
# --------------------------------------------------------------------------- #

def test_logged_eff_pvd_schema():
    data = [{"tokens": {"prover": {"input": 100, "output": 50},
                        "verifier": {"input": 60, "output": 40}},
             "cost_usd": 1.0}]
    assert _logged_eff(data) == {
        "tokens_in": 160.0, "tokens_out": 90.0,
        "tokens_total": 250.0, "cost_usd": 1.0,
    }

def test_logged_eff_pvd_missing_verifier():
    # self-play / single-attempt rows may log only a prover block
    data = [{"tokens": {"prover": {"input": 100, "output": 50}}, "cost_usd": 1.0}]
    assert _logged_eff(data) == {
        "tokens_in": 100.0, "tokens_out": 50.0,
        "tokens_total": 150.0, "cost_usd": 1.0,
    }

def test_logged_eff_usc_schema():
    data = [{"tokens": {"selector": {"input": 120, "output": 80}}, "cost_usd": 0.5}]
    assert _logged_eff(data) == {
        "tokens_in": 120.0, "tokens_out": 80.0,
        "tokens_total": 200.0, "cost_usd": 0.5,
    }

def test_logged_eff_flat_schema():
    data = [{"tokens": {"input": 100, "output": 50}, "cost_usd": 2.0}]
    assert _logged_eff(data) == {
        "tokens_in": 100.0, "tokens_out": 50.0,
        "tokens_total": 150.0, "cost_usd": 2.0,
    }

def test_logged_eff_means_over_records():
    data = [
        {"tokens": {"input": 100, "output": 50}, "cost_usd": 1.0},
        {"tokens": {"input": 200, "output": 150}, "cost_usd": 3.0},
    ]
    out = _logged_eff(data)
    assert out["tokens_in"] == 150.0
    assert out["tokens_out"] == 100.0
    assert out["cost_usd"] == 2.0


# --------------------------------------------------------------------------- #
# _logged_eff — None on missing/partial logs
# --------------------------------------------------------------------------- #

def test_logged_eff_none_when_empty():
    assert _logged_eff([]) is None

def test_logged_eff_none_when_no_tokens():
    data = [{"cost_usd": 1.0}, {"cost_usd": 2.0}]
    assert _logged_eff(data) is None

def test_logged_eff_partial_logging_returns_none():
    # second record missing cost_usd → treat whole row as unlogged
    data = [{"tokens": {"input": 100, "output": 50}, "cost_usd": 1.0},
            {"tokens": {"input": 120, "output": 70}}]
    assert _logged_eff(data) is None

def test_logged_eff_unrecognized_schema_raises():
    # has tokens + cost_usd, but tokens shape is unknown → must fail loudly,
    # not silently log zero tokens
    data = [{"tokens": {"mystery": {"in": 100}}, "cost_usd": 1.0}]
    with pytest.raises(ValueError, match="unrecognized tokens schema"):
        _logged_eff(data)


# --------------------------------------------------------------------------- #
# _eff — prefer logged, keep calls, tag source
# --------------------------------------------------------------------------- #

def test_eff_uses_modeled_when_unlogged():
    modeled = {"tokens_in": 1.0, "tokens_out": 2.0, "tokens_total": 3.0,
               "cost_usd": 0.5, "calls": 4.0}
    out = _eff(modeled, [{"cost_usd": 1.0}])  # no tokens → modeled
    assert out["eff_source"] == "modeled"
    assert out["tokens_total"] == 3.0
    assert out["calls"] == 4.0

def test_eff_prefers_logged_but_keeps_calls():
    modeled = {"tokens_in": 999, "tokens_out": 999, "tokens_total": 999,
               "cost_usd": 9.9, "calls": 7.0}
    data = [{"tokens": {"input": 100, "output": 50}, "cost_usd": 1.0}]
    out = _eff(modeled, data)
    assert out["eff_source"] == "logged"
    assert out["tokens_in"] == 100.0          # logged overrides modeled
    assert out["tokens_total"] == 150.0
    assert out["cost_usd"] == 1.0
    assert out["calls"] == 7.0                # calls always from modeled
