"""
Statistical analysis of RTTR runs: per-run accuracy and HC metrics with
Wilson 95% CIs, bootstrap 95% CI for gap, a Fisher's exact test that the
gap is non-zero, and pairwise McNemar significance.

Usage:
    python -m rttr.stats                    # per-run table with CIs
    python -m rttr.stats --pairs            # also print pairwise McNemar matrix
    python -m rttr.stats --output FILE      # write JSON to FILE
    python -m rttr.stats --n-boot N         # bootstrap iterations (default 10000)
"""

from __future__ import annotations

import argparse
import json
import math

import numpy as np
from scipy.stats import chi2 as scipy_chi2, fisher_exact

from rttr.common import DATA_DIR
from rttr.summary import RUN_META, _hc_predicate


# --------------------------------------------------------------------------- #
# Statistical helpers
# --------------------------------------------------------------------------- #

def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    """Wilson score 95% CI for proportion k/n. Returns (lo, mid, hi) in [0,100]."""
    if n == 0:
        nan = float("nan")
        return nan, nan, nan
    p = k / n
    d = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100.0 * max(0.0, center - half), 100.0 * p, 100.0 * min(1.0, center + half)


def mcnemar_p(correct_a: list[bool], correct_b: list[bool]) -> float:
    """McNemar's test p-value (continuity-corrected) for paired correctness lists."""
    a = np.asarray(correct_a, dtype=bool)
    b = np.asarray(correct_b, dtype=bool)
    n_ab = int((a & ~b).sum())
    n_ba = int((~a & b).sum())
    if n_ab + n_ba == 0:
        return 1.0
    chi2_stat = (abs(n_ab - n_ba) - 1.0) ** 2 / (n_ab + n_ba)
    return float(scipy_chi2.sf(chi2_stat, df=1))


def gap_fisher_p(hc_correct: int, hc_total: int,
                 non_hc_correct: int, non_hc_total: int) -> float:
    """Two-sided Fisher's exact p-value for the selection gap.

    Tests H0: P(correct | HC) == P(correct | non-HC) on the 2x2 table
        [[hc_correct,     hc_total - hc_correct],
         [non_hc_correct, non_hc_total - non_hc_correct]].
    Fisher's exact (rather than a normal-approximation z-test) is used because
    some non-HC complements are small. Returns NaN if either group is empty.
    """
    if hc_total == 0 or non_hc_total == 0:
        return float("nan")
    table = [[hc_correct, hc_total - hc_correct],
             [non_hc_correct, non_hc_total - non_hc_correct]]
    return float(fisher_exact(table, alternative="two-sided")[1])


def _gap_stat(records: list[dict], protocol: str) -> float:
    hc = [r for r in records if _hc_predicate(protocol, r)]
    non_hc = [r for r in records if not _hc_predicate(protocol, r)]
    if not hc or not non_hc:
        return float("nan")
    return 100.0 * (
        sum(r["correct"] for r in hc) / len(hc)
        - sum(r["correct"] for r in non_hc) / len(non_hc)
    )


def bootstrap_ci(
    records: list[dict],
    protocol: str,
    n_boot: int = 10_000,
    seed: int = 42,
) -> tuple[float, float, float]:
    """Percentile bootstrap 95% CI for gap = hc_prec − non_hc_acc.

    Uses a non-stratified bootstrap: each resample draws n records with
    replacement and re-derives the HC/non-HC split from the resample. This is
    intentional — the partition is endogenous, and we want the CI to capture
    uncertainty in both the split and the conditional accuracies.

    Degenerate resamples (all records land in one partition → NaN gap) are
    excluded. For n=198 and HC coverage in [20%, 80%] (the range observed
    across all RTTR runs), P(degenerate) < 1e-20 and the exclusion is safe.
    For heavily imbalanced splits (< ~5 minority records) this probability
    rises; callers should verify adequate coverage before trusting the CI.
    """
    observed = _gap_stat(records, protocol)
    n = len(records)
    correct = np.array([r["correct"] for r in records], dtype=bool)
    is_hc   = np.array([_hc_predicate(protocol, r) for r in records], dtype=bool)

    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))   # (n_boot, n)

    boot_correct = correct[idx]   # (n_boot, n)
    boot_hc      = is_hc[idx]     # (n_boot, n)

    hc_n   = boot_hc.sum(axis=1)                       # (n_boot,)
    nhc_n  = n - hc_n
    hc_k   = (boot_correct & boot_hc).sum(axis=1)
    nhc_k  = (boot_correct & ~boot_hc).sum(axis=1)

    valid = (hc_n > 0) & (nhc_n > 0)
    hc_p  = np.where(valid, hc_k  / np.where(hc_n  > 0, hc_n,  1), np.nan)
    nhc_p = np.where(valid, nhc_k / np.where(nhc_n > 0, nhc_n, 1), np.nan)
    gaps  = 100.0 * (hc_p - nhc_p)                     # NaN where not valid

    valid_gaps = gaps[valid]
    if valid_gaps.size == 0:
        return float("nan"), observed, float("nan")
    valid_gaps.sort()
    lo = float(valid_gaps[int(0.025 * len(valid_gaps))])
    hi = float(valid_gaps[min(int(0.975 * len(valid_gaps)), len(valid_gaps) - 1)])
    return lo, observed, hi


# --------------------------------------------------------------------------- #
# Per-run stats
# --------------------------------------------------------------------------- #

def _load_records(run_key: str) -> list[dict] | None:
    path = DATA_DIR / f"gpqa_results_{run_key}.json"
    if not path.exists():
        return None
    with open(path) as f:
        raw = json.load(f)
    return [
        r for r in raw
        if isinstance(r, dict)
        and not r.get("_meta")
        and r.get("schema_version") == "rttr-v1"
    ]


def run_stats(run_key: str, n_boot: int = 10_000) -> dict | None:
    """Compute per-run stats with CIs. Returns None if no data file exists."""
    meta = RUN_META[run_key]
    protocol = meta["protocol"]
    records = _load_records(run_key)
    if not records:
        return None
    n = len(records)
    correct = sum(r["correct"] for r in records)
    hc = [r for r in records if _hc_predicate(protocol, r)]
    non_hc = [r for r in records if not _hc_predicate(protocol, r)]
    hc_correct = sum(r["correct"] for r in hc)
    non_hc_correct = sum(r["correct"] for r in non_hc)

    acc_lo, acc, acc_hi         = wilson_ci(correct, n)
    cov_lo, cov, cov_hi         = wilson_ci(len(hc), n)
    prec_lo, prec, prec_hi      = wilson_ci(hc_correct, len(hc)) if hc else (float("nan"),) * 3
    nhc_lo, nhc, nhc_hi         = wilson_ci(non_hc_correct, len(non_hc)) if non_hc else (float("nan"),) * 3
    gap_lo, gap, gap_hi         = bootstrap_ci(records, protocol, n_boot=n_boot)
    gap_p = gap_fisher_p(hc_correct, len(hc), non_hc_correct, len(non_hc))

    return {
        "run_key": run_key,
        "n": n,
        "acc":         acc,  "acc_lo":  acc_lo,  "acc_hi":  acc_hi,
        "hc_cov":      cov,  "hc_cov_lo": cov_lo, "hc_cov_hi": cov_hi,
        "hc_prec":    prec,  "hc_prec_lo": prec_lo, "hc_prec_hi": prec_hi,
        "non_hc_acc":  nhc,  "non_hc_acc_lo": nhc_lo, "non_hc_acc_hi": nhc_hi,
        "gap":         gap,  "gap_lo":  gap_lo,  "gap_hi":  gap_hi,
        "gap_p":       gap_p,
        # Kept for pairwise alignment; stripped before JSON output.
        "_correct_mask": [r["correct"] for r in records],
        "_question_nums": [r.get("question_num") for r in records],
    }


# --------------------------------------------------------------------------- #
# Pairwise McNemar
# --------------------------------------------------------------------------- #

def _align(sa: dict, sb: dict) -> tuple[list[bool], list[bool]]:
    """Align correctness masks by question_num; fall back to positional."""
    qa, qb = sa["_question_nums"], sb["_question_nums"]
    if qa == qb:
        return sa["_correct_mask"], sb["_correct_mask"]
    lookup = dict(zip(qb, sb["_correct_mask"]))
    pairs = [(ca, lookup[q]) for q, ca in zip(qa, sa["_correct_mask"]) if q in lookup]
    if not pairs:
        return [], []
    ca, cb = zip(*pairs)
    return list(ca), list(cb)


def pairwise_mcnemar(stats_by_key: dict[str, dict]) -> dict[tuple[str, str], float]:
    keys = list(stats_by_key)
    out: dict[tuple[str, str], float] = {}
    for i, ka in enumerate(keys):
        for kb in keys[i + 1:]:
            ca, cb = _align(stats_by_key[ka], stats_by_key[kb])
            if ca:
                out[(ka, kb)] = mcnemar_p(ca, cb)
    return out


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #

def _fmt(lo: float, mid: float, hi: float) -> str:
    if math.isnan(mid):
        return "—"
    return f"{mid:5.1f} [{lo:5.1f},{hi:5.1f}]"


def _stars(p: float) -> str:
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "** "
    if p < 0.05:
        return "*  "
    return "   "


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description="RTTR per-run stats with 95% CIs")
    parser.add_argument("--pairs", action="store_true",
                        help="Print pairwise McNemar significance matrix")
    parser.add_argument("--output", metavar="FILE",
                        help="Write per-run stats JSON to FILE")
    parser.add_argument("--n-boot", type=int, default=10_000,
                        help="Bootstrap iterations for gap CI (default: 10000)")
    args = parser.parse_args()

    available = [k for k in RUN_META if _load_records(k) is not None]
    stats_by_key: dict[str, dict] = {}
    for k in available:
        s = run_stats(k, n_boot=args.n_boot)
        if s:
            stats_by_key[k] = s

    # Per-run table
    print()
    hdr = f"{'run_key':20}  {'n':>3}  {'acc (95% CI)':^18}  {'hc_cov':^18}  {'hc_prec':^18}  {'gap (boot)':^18}"
    print(hdr)
    print("-" * len(hdr))
    for k, s in stats_by_key.items():
        print(
            f"{k:20}  {s['n']:>3}  "
            f"{_fmt(s['acc_lo'],  s['acc'],    s['acc_hi']):^18}  "
            f"{_fmt(s['hc_cov_lo'], s['hc_cov'], s['hc_cov_hi']):^18}  "
            f"{_fmt(s['hc_prec_lo'], s['hc_prec'], s['hc_prec_hi']):^18}  "
            f"{_fmt(s['gap_lo'],  s['gap'],    s['gap_hi']):^18}"
        )
    print()
    print("Wilson 95% CI for acc/hc_cov/hc_prec; percentile bootstrap for gap.")

    # Pairwise McNemar
    if args.pairs:
        pairs = pairwise_mcnemar(stats_by_key)
        keys = list(stats_by_key)
        cw = max(len(k) for k in keys) + 2
        print()
        print("Pairwise McNemar p-values (* p<.05  ** p<.01  *** p<.001):")
        print(f"{'':20}", end="")
        for k in keys:
            print(f"  {k[:cw]:>{cw}}", end="")
        print()
        for ka in keys:
            print(f"{ka:20}", end="")
            for kb in keys:
                if ka == kb:
                    print(f"  {'—':>{cw}}", end="")
                else:
                    key = (ka, kb) if (ka, kb) in pairs else (kb, ka)
                    p = pairs.get(key)
                    if p is None:
                        print(f"  {'n/a':>{cw}}", end="")
                    elif p < 0.001:
                        print(f"  {'<.001'+_stars(p):>{cw}}", end="")
                    else:
                        print(f"  {f'{p:.3f}'+_stars(p):>{cw}}", end="")
            print()

    # JSON output (strip internal _-prefixed fields)
    if args.output:
        out = [
            {k: v for k, v in s.items() if not k.startswith("_")}
            for s in stats_by_key.values()
        ]
        with open(args.output, "w") as f:
            json.dump(out, f, indent=2)
            f.write("\n")
        print(f"\n→ {args.output}")


if __name__ == "__main__":
    main()
