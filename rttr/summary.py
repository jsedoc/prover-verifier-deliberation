"""
Build a Table-3-style summary from RTTR-v1 result files.

Writes:  tables/generated/rttr_summary.json

Schema (one row per RTTR run):
  key, display_name, prover, verifier, dataset (GPQA Diamond),
  method, method_family,
  n, acc, hc_cov, hc_prec, non_hc_acc, gap,
  avg_attempts, avg_rounds, avg_trials, calls,
  tokens_in, tokens_out, tokens_total, cost_usd,
  source_json, schema_version

This is intentionally separate from tables/build_summary.py so the
existing paper-table pipeline keeps working while we audit the
re-runs. Once we're confident in the new numbers, we'll either merge
or replace.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

from rttr.common import DATA_DIR, REPO


SUMMARY_PATH = REPO / "tables" / "generated" / "rttr_summary.json"


# Display metadata for each RTTR run_key. Keep this small — numbers
# come from the data; only labels live here.
RUN_META = {
    "pvd_standard":  dict(
        display_name="PVD (standard)",
        method="PVD",
        prover="Sonnet 4.6", verifier="Haiku 4.5",
        method_family="pvd_sonnet",
        protocol="pvd"),
    "pvd_min1": dict(
        display_name="PVD (min-1 challenge)",
        method=r"PVD$^\dagger$",
        prover="Sonnet 4.6", verifier="Haiku 4.5",
        method_family="pvd_sonnet",
        protocol="pvd"),
    "pvd_self": dict(
        display_name="PVD (self-play)",
        method="PVD (self)",
        prover="Sonnet 4.6", verifier="Sonnet 4.6",
        method_family="pvd_sonnet",
        protocol="pvd"),
    "pvd_retry": dict(
        display_name="PVD+retry",
        method="PVD+retry",
        prover="Sonnet 4.6", verifier="Haiku 4.5",
        method_family="pvd_sonnet",
        protocol="pvd"),
    "debate": dict(
        display_name="Debate (3$\\times$2)",
        method="Debate",
        prover="Sonnet 4.6", verifier=None,
        method_family="baseline",
        protocol="debate"),
    "reflexion": dict(
        display_name="Reflexion",
        method="Reflexion",
        prover="Sonnet 4.6", verifier=None,
        method_family="baseline",
        protocol="reflexion"),
    "sc_epoch": dict(
        display_name="SC ($k{=}8$)$^*$",
        method=r"SC$^*$ (k=8)",
        prover="Sonnet 4.6", verifier=None,
        method_family="baseline",
        protocol="sc"),
    "usc": dict(
        display_name="USC ($k{=}8$)",
        method="USC (k=8)",
        prover="Sonnet 4.6", verifier="Sonnet 4.6",
        method_family="baseline",
        protocol="usc"),
    "single_call": dict(
        display_name="Single-call PVD",
        method="Single-call",
        prover="Sonnet 4.6", verifier="Sonnet 4.6",
        method_family="single_call",
        protocol="single_call"),
}


def _hc_predicate(protocol: str, record: dict) -> bool:
    if protocol == "pvd":
        return record.get("outcome") == "accept_no_change"
    if protocol == "debate":
        return record.get("consensus_reached") is True
    if protocol == "reflexion":
        return record.get("final_stable") and not record.get("answer_changed")
    if protocol in ("sc", "usc"):
        return record.get("full_consensus") is True
    if protocol == "single_call":
        return record.get("outcome") == "accept_no_change"
    return False


def _tokens(record: dict) -> tuple[int, int]:
    """Return (input, output) tokens for the question record."""
    t = record.get("tokens", {})
    if "prover" in t:
        return (t["prover"]["input"] + t.get("verifier", {}).get("input", 0),
                t["prover"]["output"] + t.get("verifier", {}).get("output", 0))
    if "selector" in t:
        return (t["selector"]["input"], t["selector"]["output"])
    return (t.get("input", 0), t.get("output", 0))


def _calls(protocol: str, record: dict) -> float:
    if protocol == "pvd":
        if record.get("attempts"):
            return float(sum(
                1 for attempt in record["attempts"]
                for turn in attempt.get("transcript", [])
                if turn.get("role") in ("prover", "verifier")
            ))
        # Fallback for compact PVD rows: each recorded round is a verifier
        # turn, and each attempt has one fewer prover follow-up than verifier
        # turns, yielding 2 * total_rounds calls.
        return 2.0 * record.get("total_rounds", record.get("rounds_used", 0))
    if protocol == "debate":
        return record.get("num_agents", 3) * (1 + record.get("num_rounds", 2))
    if protocol == "reflexion":
        trials = record.get("trials_used", 1)
        unstable = sum(1 for t in record.get("trials", []) if not t.get("stable"))
        return 2 * trials + unstable
    if protocol == "sc":
        return float(record.get("k", 8))
    if protocol == "usc":
        # k SC samples (we reused them) + 1 selector pass
        return float(record.get("k", 8) + 1)
    if protocol == "single_call":
        return 1.0   # by definition
    return float("nan")


def stats_for(run_key: str) -> dict | None:
    meta = RUN_META[run_key]
    path = DATA_DIR / f"gpqa_results_{run_key}.json"
    if not path.exists():
        return None
    with open(path) as f:
        raw = json.load(f)
    file_meta = next((r for r in raw if isinstance(r, dict) and r.get("_meta")), None)
    records = [r for r in raw if isinstance(r, dict) and not r.get("_meta")
               and r.get("schema_version") == "rttr-v1"]
    n = len(records)
    if n == 0:
        return None
    correct = sum(r["correct"] for r in records)
    hc = [r for r in records if _hc_predicate(meta["protocol"], r)]
    non_hc = [r for r in records if not _hc_predicate(meta["protocol"], r)]
    hc_cov = 100 * len(hc) / n
    hc_prec = 100 * sum(r["correct"] for r in hc) / len(hc) if hc else float("nan")
    non_hc_acc = 100 * sum(r["correct"] for r in non_hc) / len(non_hc) if non_hc else float("nan")
    gap = hc_prec - non_hc_acc

    # Aggregates
    tokens_in  = sum(_tokens(r)[0] for r in records)
    tokens_out = sum(_tokens(r)[1] for r in records)
    cost = sum(r.get("cost_usd", 0) + r.get("selector_cost_usd", 0) for r in records)
    calls = sum(_calls(meta["protocol"], r) for r in records) / n

    # USC inherits the SC sample cost: it reused those samples, so a fair
    # comparison includes them.
    if meta["protocol"] == "usc":
        sc_path = DATA_DIR / "gpqa_results_sc_epoch.json"
        if sc_path.exists():
            with open(sc_path) as f:
                sc_raw = json.load(f)
            sc_records = [r for r in sc_raw if isinstance(r, dict) and not r.get("_meta")]
            sc_by_q = {r["question_num"]: r for r in sc_records}
            sc_in = sc_out = sc_cost = 0
            for r in records:
                src = sc_by_q.get(r["question_num"])
                if src:
                    ti, to = _tokens(src)
                    sc_in  += ti
                    sc_out += to
                    sc_cost += src.get("cost_usd", 0)
            tokens_in  += sc_in
            tokens_out += sc_out
            cost       += sc_cost

    out = dict(
        key=f"rttr_{run_key}",
        run_key=run_key,
        display_name=meta["display_name"],
        method=meta["method"],
        prover=meta["prover"],
        verifier=meta["verifier"],
        method_family=meta["method_family"],
        protocol=meta["protocol"],
        dataset="GPQA Diamond",
        n=n,
        acc=100 * correct / n,
        hc_cov=hc_cov, hc_prec=hc_prec, non_hc_acc=non_hc_acc, gap=gap,
        calls=calls,
        tokens_in=tokens_in / n,           # per-question avg
        tokens_out=tokens_out / n,
        tokens_total=(tokens_in + tokens_out) / n,
        cost_usd=cost / n,                 # per-question avg
        total_cost_usd=cost,
        source_json=f"data/gpqa_results_{run_key}.json",
        schema_version="rttr-v1",
        partial=(n < 198),
    )
    # Protocol-specific extras
    if meta["protocol"] == "pvd":
        out["avg_attempts"] = sum(r.get("num_attempts", 1) for r in records) / n
        out["avg_rounds"]   = sum(r.get("total_rounds", 0) for r in records) / n
    elif meta["protocol"] == "reflexion":
        out["avg_trials"] = sum(r.get("trials_used", 1) for r in records) / n
    elif meta["protocol"] == "debate":
        first = records[0]
        out["num_agents"] = first.get("num_agents", 3)
        out["num_rounds"] = first.get("num_rounds", 2)
    # File-level meta
    if file_meta:
        out["git_sha"] = file_meta.get("git_sha")
        out["started_at"] = file_meta.get("started_at")
        out["finished_at"] = file_meta.get("finished_at")
    return out


def _clean_nan(obj):
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_nan(v) for v in obj]
    if isinstance(obj, float) and math.isnan(obj):
        return None
    return obj


def main():
    rows = []
    for run_key in RUN_META:
        s = stats_for(run_key)
        if s is not None:
            rows.append(s)
    rows = _clean_nan(rows)
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SUMMARY_PATH, "w") as f:
        json.dump(rows, f, indent=2)
        f.write("\n")
    print(f"  → {SUMMARY_PATH}  ({len(rows)} runs)")
    # ASCII recap
    print()
    print(f"{'key':25} {'n':>4} {'acc':>6} {'hc_cov':>7} {'hc_prec':>8} "
          f"{'gap':>7} {'calls':>6} {'$':>8}")
    for r in rows:
        def _f(x, w=6, p=1):
            return f"{'—':>{w}}" if x is None else f"{x:>{w}.{p}f}"
        partial = " (partial)" if r.get("partial") else ""
        print(f"{r['key']:25} {r['n']:>4} {_f(r['acc'])} "
              f"{_f(r['hc_cov'])} {_f(r['hc_prec'])} {_f(r['gap'])} "
              f"{r['calls']:>6.1f} ${r['total_cost_usd']:>7.2f}{partial}")


if __name__ == "__main__":
    main()
