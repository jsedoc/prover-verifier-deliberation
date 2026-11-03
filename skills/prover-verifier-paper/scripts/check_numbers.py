#!/usr/bin/env python3
"""Deterministic arithmetic and statistics checks for paper claims (stdlib only).

Every op prints one JSON object. Add --claimed X to test an equality claim
(tolerance = half a unit in the last digit of X, or --tol, or --approx for "~X"),
or --min / --max to test inequality claims ("more than a third", "p < 0.01").

  diff A B                      A - B                      (e.g. a gap in points)
  ratio A B                     A / B
  pct-change NEW OLD            100*(NEW-OLD)/OLD          relative change, also reports NEW-OLD
  reduction OLD NEW             100*(OLD-NEW)/OLD          relative reduction
  pct K N                       100*K/N
  mean V [V ...] | sum V [V ...] | sd V [V ...]
  compare V                     the value itself (rounding / restatement checks)
  wilson K N [--level 0.95]     Wilson score interval for K/N
  binom K N [--p0 0.5]          exact two-sided binomial test
  fisher A B C D                Fisher exact test, table [[A,B],[C,D]]
  two-prop K1 N1 K2 N2          two-proportion z-test (unpaired)
  mcnemar B C                   exact McNemar test on discordant counts (paired)
  welch M1 SD1 N1 M2 SD2 N2     Welch t-test from summary statistics
  paired-t MEAN_DIFF SD_DIFF N  paired t-test from summary statistics
  cohen-d M1 SD1 N1 M2 SD2 N2   Cohen's d (pooled SD)
  ci-overlap L1 U1 L2 U2        do two intervals overlap?
  expr "EXPR"                   safe arithmetic: + - * / ** ( ) sqrt log exp abs min max round mean sum

Examples:
  check_numbers.py diff 84.2 52.2 --claimed 32.0
  check_numbers.py reduction 25.7 18.6 --min 33.33      # "cuts error by more than a third"
  check_numbers.py mcnemar 41 24 --max 0.01             # "significant, p < 0.01"
  check_numbers.py expr "(107*0.963 + 22*0.591)/129*100" --claimed 89.9
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import sys
from statistics import NormalDist

# ---------------------------------------------------------------- distributions


def _lcomb(n, k):
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def binom_pmf(k, n, p):
    if p <= 0.0:
        return 1.0 if k == 0 else 0.0
    if p >= 1.0:
        return 1.0 if k == n else 0.0
    return math.exp(_lcomb(n, k) + k * math.log(p) + (n - k) * math.log1p(-p))


def binom_test(k, n, p0=0.5):
    """Exact two-sided binomial test (same convention as scipy.stats.binomtest)."""
    d = binom_pmf(k, n, p0)
    tol = 1 + 1e-7
    return min(1.0, sum(binom_pmf(i, n, p0) for i in range(n + 1) if binom_pmf(i, n, p0) <= d * tol))


def fisher_exact(a, b, c, d):
    r1, r2, c1 = a + b, c + d, a + c
    n = r1 + r2
    lo, hi = max(0, c1 - r2), min(r1, c1)

    def lp(x):
        return _lcomb(r1, x) + _lcomb(r2, c1 - x) - _lcomb(n, c1)

    obs = lp(a)
    p = sum(math.exp(lp(x)) for x in range(lo, hi + 1) if lp(x) <= obs + 1e-7)
    return min(1.0, p)


def _betacf(a, b, x, maxit=500, eps=3e-15):
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, maxit + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            break
    return h


def betainc(a, b, x):
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    bt = math.exp(lbt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_two_sided(t, df):
    return betainc(df / 2.0, 0.5, df / (df + t * t))


def norm_two_sided(z):
    return math.erfc(abs(z) / math.sqrt(2.0))


# ---------------------------------------------------------------- safe expressions

_FUNCS = {
    "sqrt": math.sqrt, "log": math.log, "log10": math.log10, "exp": math.exp, "abs": abs,
    "min": min, "max": max, "round": round,
    "mean": lambda *v: sum(v) / len(v), "sum": lambda *v: sum(v),
}
_CONSTS = {"pi": math.pi, "e": math.e}


def safe_eval(expr: str) -> float:
    tree = ast.parse(expr, mode="eval")

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.Name) and node.id in _CONSTS:
            return _CONSTS[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            v = ev(node.operand)
            return v if isinstance(node.op, ast.UAdd) else -v
        if isinstance(node, ast.BinOp):
            a, b = ev(node.left), ev(node.right)
            op = node.op
            if isinstance(op, ast.Add): return a + b
            if isinstance(op, ast.Sub): return a - b
            if isinstance(op, ast.Mult): return a * b
            if isinstance(op, ast.Div): return a / b
            if isinstance(op, ast.FloorDiv): return a // b
            if isinstance(op, ast.Mod): return a % b
            if isinstance(op, ast.Pow):
                if abs(b) > 64 or abs(a) > 1e12:
                    raise ValueError("exponent too large")
                return a ** b
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
            return _FUNCS[node.func.id](*[ev(x) for x in node.args])
        raise ValueError(f"unsupported expression element: {ast.dump(node)[:60]}")

    return float(ev(tree))


# ---------------------------------------------------------------- checks


def _decimals(s: str) -> int:
    s = s.strip().lower()
    if "e" in s:
        return 6
    return len(s.split(".", 1)[1]) if "." in s else 0


def _check(value, ns):
    out = {}
    if ns.claimed is not None:
        claimed = float(ns.claimed)
        tol = ns.tol if ns.tol is not None else 0.5 * 10 ** (-_decimals(ns.claimed)) + 1e-9
        if ns.approx:
            tol = max(tol, 0.1 * abs(claimed))
        out = {"claimed": claimed, "tol": round(tol, 10), "match": abs(value - claimed) <= tol,
               "delta": round(value - claimed, 10)}
    if ns.min is not None or ns.max is not None:
        ok = True
        if ns.min is not None:
            ok = ok and value >= ns.min
            out["min"] = ns.min
        if ns.max is not None:
            ok = ok and value <= ns.max
            out["max"] = ns.max
        out["within_bounds"] = ok
        out["match"] = ok and out.get("match", True)
    return out


def _r(x, k=6):
    return None if x is None else round(x, k)


def compute(ns):
    op, a = ns.op, ns.args
    det = {}
    if op == "diff":
        x, y = map(float, a); value = x - y
    elif op == "ratio":
        x, y = map(float, a); value = x / y
    elif op == "pct-change":
        new, old = map(float, a); value = 100.0 * (new - old) / old
        det = {"absolute_change": _r(new - old), "note": "relative %; absolute change is in the input's units (pp if inputs are %)"}
    elif op == "reduction":
        old, new = map(float, a); value = 100.0 * (old - new) / old
        det = {"absolute_reduction": _r(old - new)}
    elif op == "pct":
        k, n = map(float, a); value = 100.0 * k / n
    elif op in ("mean", "sum", "sd"):
        v = [float(x) for x in a]
        if op == "mean": value = sum(v) / len(v)
        elif op == "sum": value = sum(v)
        else:
            m = sum(v) / len(v)
            value = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))
    elif op == "compare":
        value = float(a[0])
    elif op == "wilson":
        k, n = int(a[0]), int(a[1])
        z = NormalDist().inv_cdf(1 - (1 - ns.level) / 2)
        p = k / n
        den = 1 + z * z / n
        centre = (p + z * z / (2 * n)) / den
        half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
        value = 100 * p
        det = {"proportion_pct": _r(100 * p, 4), "lower_pct": _r(100 * (centre - half), 4),
               "upper_pct": _r(100 * (centre + half), 4), "level": ns.level}
    elif op == "binom":
        k, n = int(a[0]), int(a[1]); value = binom_test(k, n, ns.p0)
        det = {"p0": ns.p0, "observed": k / n}
    elif op == "fisher":
        A, B, C, D = map(int, a); value = fisher_exact(A, B, C, D)
        det = {"table": [[A, B], [C, D]],
               "odds_ratio": _r((A * D) / (B * C)) if B * C else None,
               "rate_row1": _r(A / (A + B)) if A + B else None, "rate_row2": _r(C / (C + D)) if C + D else None}
    elif op == "two-prop":
        k1, n1, k2, n2 = int(a[0]), int(a[1]), int(a[2]), int(a[3])
        p1, p2 = k1 / n1, k2 / n2
        pp = (k1 + k2) / (n1 + n2)
        se = math.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2))
        z = (p1 - p2) / se if se else float("inf")
        value = norm_two_sided(z)
        det = {"p1": _r(p1), "p2": _r(p2), "z": _r(z), "note": "unpaired; use mcnemar for the same items under two conditions"}
    elif op == "mcnemar":
        b, c = int(a[0]), int(a[1])
        value = binom_test(min(b, c), b + c, 0.5) if b + c else 1.0
        chi2 = (abs(b - c) - 1) ** 2 / (b + c) if b + c else 0.0
        det = {"discordant": [b, c], "exact_p": _r(value, 8), "chi2_cc": _r(chi2),
               "chi2_cc_p": _r(math.erfc(math.sqrt(chi2 / 2)), 8)}
    elif op == "welch":
        m1, s1, n1, m2, s2, n2 = map(float, a)
        v1, v2 = s1 * s1 / n1, s2 * s2 / n2
        t = (m1 - m2) / math.sqrt(v1 + v2)
        df = (v1 + v2) ** 2 / (v1 * v1 / (n1 - 1) + v2 * v2 / (n2 - 1))
        value = t_two_sided(t, df)
        det = {"t": _r(t), "df": _r(df, 3), "mean_diff": _r(m1 - m2)}
    elif op == "paired-t":
        md, sd, n = float(a[0]), float(a[1]), float(a[2])
        t = md / (sd / math.sqrt(n)); df = n - 1
        value = t_two_sided(t, df)
        det = {"t": _r(t), "df": df}
    elif op == "cohen-d":
        m1, s1, n1, m2, s2, n2 = map(float, a)
        sp = math.sqrt(((n1 - 1) * s1 * s1 + (n2 - 1) * s2 * s2) / (n1 + n2 - 2))
        value = (m1 - m2) / sp
        det = {"pooled_sd": _r(sp)}
    elif op == "ci-overlap":
        l1, u1, l2, u2 = map(float, a)
        ov = max(0.0, min(u1, u2) - max(l1, l2))
        value = 1.0 if ov > 0 else 0.0
        det = {"overlap_width": _r(ov), "note": "overlapping 95% CIs do not by themselves imply p > 0.05"}
    elif op == "expr":
        value = safe_eval(" ".join(a))
    else:
        raise ValueError(f"unknown op {op}")
    out = {"op": op, "args": a, "value": _r(value, 8)}
    if det:
        out["details"] = det
    out.update(_check(value, ns))
    return out


ARITY = {"diff": 2, "ratio": 2, "pct-change": 2, "reduction": 2, "pct": 2, "compare": 1, "wilson": 2,
         "binom": 2, "fisher": 4, "two-prop": 4, "mcnemar": 2, "welch": 6, "paired-t": 3, "cohen-d": 6,
         "ci-overlap": 4}


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("op", choices=sorted(list(ARITY) + ["mean", "sum", "sd", "expr"]))
    p.add_argument("args", nargs="+")
    p.add_argument("--claimed", help="value the paper states (string, so its precision sets the tolerance)")
    p.add_argument("--tol", type=float)
    p.add_argument("--approx", action="store_true", help='claim is approximate ("~X", "about X"): 10%% relative tolerance')
    p.add_argument("--min", type=float, help="value must be >= this")
    p.add_argument("--max", type=float, help="value must be <= this")
    p.add_argument("--level", type=float, default=0.95)
    p.add_argument("--p0", type=float, default=0.5)
    return p


def run(argv):
    """In-process entry point used by ledger.py auto-checks. Never raises."""
    try:
        ns = build_parser().parse_args(argv)
        need = ARITY.get(ns.op)
        if need is not None and len(ns.args) != need:
            return {"op": ns.op, "args": ns.args, "error": f"{ns.op} takes {need} numbers, got {len(ns.args)}"}
        return compute(ns)
    except SystemExit:
        return {"args": argv, "error": "could not parse arguments"}
    except Exception as e:  # noqa: BLE001 — report, don't crash the ledger
        return {"args": argv, "error": f"{type(e).__name__}: {e}"}


def main():
    ns = build_parser().parse_args()
    need = ARITY.get(ns.op)
    if need is not None and len(ns.args) != need:
        print(json.dumps({"op": ns.op, "error": f"{ns.op} takes {need} numbers, got {len(ns.args)}"}))
        sys.exit(2)
    try:
        print(json.dumps(compute(ns)))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"op": ns.op, "args": ns.args, "error": f"{type(e).__name__}: {e}"}))
        sys.exit(2)


if __name__ == "__main__":
    main()
