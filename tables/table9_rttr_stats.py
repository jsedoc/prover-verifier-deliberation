"""
Table 9 (tab:rttr-stats) — Clean RTTR runs with 95% confidence intervals.

Writes:
    stdout                                  — ASCII preview table
    tables/generated/tab_rttr_stats.tex     — LaTeX table environment

Reads tables/generated/rttr_stats.json, produced by:
    python -m rttr.stats --output tables/generated/rttr_stats.json

Acc / HC-Cov / HC-Prec carry Wilson 95% intervals; Gap carries a percentile
bootstrap interval. A Gap CI that excludes 0 means the high-confidence subset
is significantly more accurate than its complement.

Run from worktree root:
    python tables/table9_rttr_stats.py
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tables.utils import (
    TEX_DIR, print_table, build_tabular, build_table_tex, write_tex, tex_row,
)

STATS_PATH = os.path.join(TEX_DIR, "rttr_stats.json")

# Row order and display names. Runs absent from the JSON are skipped.
ROW_ORDER = [
    ("pvd_standard", "PVD (standard)"),
    ("pvd_min1",     "PVD (min-1)"),
    ("pvd_self",     "PVD (self-play)"),
    ("pvd_retry",    "PVD+retry"),
    ("debate",       "Debate (3$\\times$2)"),
    ("reflexion",    "Reflexion"),
    ("sc_epoch",     "SC ($k{=}8$)$^*$"),
    ("usc",          "USC ($k{=}8$)"),
    ("single_call",  "Single-call PVD"),
]


def _ci(mid, lo, hi, signed=False) -> str:
    """Format 'mid [lo, hi]' to one decimal. When signed, every component
    carries an explicit sign (so a negative mid renders '-4.0', never '+-4.0')."""
    if mid is None:
        return "\\textemdash"
    spec = "{:+.1f}" if signed else "{:.1f}"
    return f"{spec.format(mid)} [{spec.format(lo)}, {spec.format(hi)}]"


def _pval(p, tex=False) -> str:
    """Format a p-value: em-dash if missing, '<0.001' below threshold, else 3dp."""
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return "\\textemdash" if tex else "—"
    if p < 0.001:
        return "$<$0.001" if tex else "<0.001"
    return f"{p:.3f}"


def _rows(stats_by_key: dict) -> list:
    rows = []
    for key, label in ROW_ORDER:
        s = stats_by_key.get(key)
        if s is None:
            continue
        rows.append((label, s))
    return rows


def main():
    with open(STATS_PATH) as f:
        stats = json.load(f)
    stats_by_key = {s["run_key"]: s for s in stats}
    rows = _rows(stats_by_key)

    # ── ASCII stdout ──────────────────────────────────────────────────────────
    headers = ["Run", "Acc", "HC-Cov", "HC-Prec", "Gap (95% CI)", "Gap p"]
    ascii_rows = []
    for label, s in rows:
        ascii_rows.append([
            label.replace("$\\times$", "x").replace("$^*$", "*")
                 .replace("$k{=}8$", "k=8"),
            _ci(s["acc"], s["acc_lo"], s["acc_hi"]),
            _ci(s["hc_cov"], s["hc_cov_lo"], s["hc_cov_hi"]),
            _ci(s["hc_prec"], s["hc_prec_lo"], s["hc_prec_hi"]),
            _ci(s["gap"], s["gap_lo"], s["gap_hi"], signed=True),
            _pval(s.get("gap_p")),
        ])
    print_table(headers, ascii_rows,
                "Table 9 — Clean RTTR Runs with 95% CIs (tab:rttr-stats)")
    print()
    print("Acc/HC-Cov/HC-Prec: Wilson 95% CI; Gap: percentile bootstrap CI.")
    print("Gap p: two-sided Fisher's exact test (H0: HC and non-HC equally accurate).")

    # ── LaTeX output ──────────────────────────────────────────────────────────
    header_rows = [tex_row(
        r"\textbf{Run}", r"\textbf{Acc}", r"\textbf{HC-Cov}",
        r"\textbf{HC-Prec}", r"\textbf{Gap (95\% CI)}", r"\textbf{Gap $p$}",
    )]
    body_rows = [
        tex_row(
            label,
            _ci(s["acc"], s["acc_lo"], s["acc_hi"]),
            _ci(s["hc_cov"], s["hc_cov_lo"], s["hc_cov_hi"]),
            _ci(s["hc_prec"], s["hc_prec_lo"], s["hc_prec_hi"]),
            _ci(s["gap"], s["gap_lo"], s["gap_hi"], signed=True),
            _pval(s.get("gap_p"), tex=True),
        )
        for label, s in rows
    ]

    n = rows[0][1]["n"] if rows else 198
    caption = (
        r"Clean RTTR runs on GPQA Diamond ($n{=}" + str(n) + r"$) with 95\% "
        r"confidence intervals and a gap significance test. \textbf{Acc}, "
        r"\textbf{HC-Cov}, and \textbf{HC-Prec} use Wilson score intervals; "
        r"\textbf{Gap} (HC-Prec minus non-HC accuracy) uses a percentile "
        r"bootstrap (10{,}000 resamples). \textbf{Gap $p$} is a two-sided "
        r"Fisher's exact test of $H_0$: HC and non-HC subsets are equally "
        r"accurate. A Gap CI excluding 0 (equivalently $p<0.05$) indicates the "
        r"high-confidence subset is significantly more accurate than its "
        r"complement. $^*$: SC with extended thinking (Epoch AI benchmark)."
    )
    tabular = build_tabular(
        col_spec="lccccc",
        header_rows=header_rows,
        body_rows=body_rows,
    )
    tex = build_table_tex(caption, "tab:rttr-stats", tabular, tabcolsep="4pt")
    write_tex("tab_rttr_stats.tex", tex)


if __name__ == "__main__":
    main()
