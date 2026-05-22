"""
Print a Table-3-style summary across all RTTR runs in data/.

Usage:
    python -m rttr.report

Reads every data/gpqa_results_<run_key>.json with schema_version == rttr-v1
and prints an ASCII summary table. Falls back gracefully if some runs are
missing.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from rttr.common import DATA_DIR, REPO


RUN_ORDER = [
    "pvd_standard",
    "pvd_min1",
    "pvd_self",
    "pvd_retry",
    "debate",
    "reflexion",
]


def _load(run_key: str):
    path = DATA_DIR / f"gpqa_results_{run_key}.json"
    if not path.exists():
        return None, None
    with open(path) as f:
        data = json.load(f)
    meta = None
    records = []
    for entry in data:
        if isinstance(entry, dict) and entry.get("_meta"):
            meta = entry
        else:
            records.append(entry)
    return meta, records


def _hc_predicate(run_key: str, record: dict) -> bool:
    """ANC / consensus / stable+unchanged depending on protocol."""
    if run_key.startswith("pvd_"):
        return record.get("outcome") == "accept_no_change"
    if run_key == "debate":
        return record.get("consensus_reached") is True
    if run_key == "reflexion":
        return record.get("final_stable") and not record.get("answer_changed")
    return False


def stats(run_key: str, records: list[dict]) -> dict:
    n = len(records)
    if n == 0:
        return {}
    correct = sum(r["correct"] for r in records)
    hc = [r for r in records if _hc_predicate(run_key, r)]
    non_hc = [r for r in records if not _hc_predicate(run_key, r)]
    hc_cov = 100 * len(hc) / n
    hc_prec = 100 * sum(r["correct"] for r in hc) / len(hc) if hc else float("nan")
    non_hc_acc = 100 * sum(r["correct"] for r in non_hc) / len(non_hc) if non_hc else float("nan")
    tokens_in = sum((r.get("tokens", {}).get("prover", {}).get("input", 0)
                     + r.get("tokens", {}).get("verifier", {}).get("input", 0)
                     + (r.get("tokens", {}).get("input", 0) if "prover" not in r.get("tokens", {}) else 0))
                    for r in records)
    tokens_out = sum((r.get("tokens", {}).get("prover", {}).get("output", 0)
                      + r.get("tokens", {}).get("verifier", {}).get("output", 0)
                      + (r.get("tokens", {}).get("output", 0) if "prover" not in r.get("tokens", {}) else 0))
                     for r in records)
    cost = sum(r.get("cost_usd", 0) for r in records)
    # Protocol-specific aggregates
    extra = {}
    if run_key.startswith("pvd_"):
        extra["avg_attempts"] = sum(r.get("num_attempts", 1) for r in records) / n
        extra["avg_rounds"]   = sum(r.get("total_rounds", 0) for r in records) / n
    elif run_key == "debate":
        extra["calls_per_q"] = records[0].get("num_agents", 3) * (1 + records[0].get("num_rounds", 2))
    elif run_key == "reflexion":
        extra["avg_trials"] = sum(r.get("trials_used", 1) for r in records) / n
    return {
        "n": n, "acc": 100*correct/n,
        "hc_cov": hc_cov, "hc_prec": hc_prec, "non_hc_acc": non_hc_acc,
        "gap": hc_prec - non_hc_acc,
        "tokens_in": tokens_in, "tokens_out": tokens_out,
        "cost_usd": cost, **extra,
    }


def main():
    print()
    print(f"{'run':16}  {'n':>4} {'acc':>6} {'hc_cov':>7} {'hc_prec':>8} "
          f"{'gap':>7} {'tok_in':>9} {'tok_out':>9} {'$':>8}  notes")
    print("-" * 110)
    for run_key in RUN_ORDER:
        meta, records = _load(run_key)
        if records is None:
            print(f"{run_key:16}  (no data file)")
            continue
        s = stats(run_key, records)
        if not s:
            print(f"{run_key:16}  (empty)")
            continue
        notes = []
        if run_key.startswith("pvd_"):
            notes.append(f"att={s['avg_attempts']:.2f} rnd={s['avg_rounds']:.2f}")
        elif run_key == "reflexion":
            notes.append(f"trials={s['avg_trials']:.2f}")
        partial = (s["n"] < 198)
        if partial:
            notes.append(f"PARTIAL {s['n']}/198")
        print(f"{run_key:16}  {s['n']:>4} "
              f"{s['acc']:>5.1f}% {s['hc_cov']:>6.1f}% {s['hc_prec']:>7.1f}% "
              f"{s['gap']:>+6.1f} "
              f"{s['tokens_in']:>9,} {s['tokens_out']:>9,} "
              f"${s['cost_usd']:>7.2f}  {' · '.join(notes)}")
    print()
    print("HC signal: ANC (PVD) | consensus (Debate) | stable+unchanged (Reflexion)")
    print("Gap      = HC-Prec − Non-HC Acc  (pp)")


if __name__ == "__main__":
    main()
