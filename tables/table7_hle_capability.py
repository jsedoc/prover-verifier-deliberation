"""
Table 7 (tab:hle-capability) — HLE ANC gap by model pairing.

Writes:
    stdout                                          — ASCII preview table
    tables/generated/tab_hle_capability.tex         — LaTeX table environment

All numbers come from tables/generated/summary.json.
Rebuild the summary first if the raw HLE result JSONs change:
    python tables/build_summary.py

Precision is controlled by the PREC dict at the top of this file.

Run from worktree root:
    python tables/table7_hle_capability.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tables.utils import (
    print_table, fmt, fmt_gap,
    tex_pct, tex_gap, tex_row,
    build_tabular, build_table_tex, write_tex, DEFAULT_PREC,
)
from tables.summary import load_summary

# ── Precision control ─────────────────────────────────────────────────────────
PREC = {**DEFAULT_PREC}

# Order of rows by `key` from summary.json
ROW_KEYS = [
    "hle_pvd_sonnet_haiku",
    "hle_pvd_opus_sonnet",
    "hle_pvd_gpt55_gemini",
]


def main():
    summary = load_summary()
    idx     = {r["key"]: r for r in summary}
    rows_data = [idx[k] for k in ROW_KEYS if k in idx]

    # ── ASCII stdout ──────────────────────────────────────────────────────────
    headers = ["Prover", "Verifier", "n", "Acc", "HC-Cov", "HC-Prec",
               "Non-ANC Acc", "Gap"]
    rows = []
    for r in rows_data:
        rows.append([
            r["prover"], r["verifier"],
            str(r["n"]),
            fmt(r["acc"],        PREC["acc"]),
            fmt(r["hc_cov"],     PREC["hc_cov"]),
            fmt(r["hc_prec"],    PREC["hc_prec"]),
            fmt(r["non_hc_acc"], PREC["hc_prec"]),
            fmt_gap(r["gap"]) + "pp",
        ])

    print_table(headers, rows,
                "Table 7 — HLE ANC Gap by Model Pairing (tab:hle-capability)")

    print("The inverted gap for Sonnet / Haiku is the empirical signature")
    print("of an empty effective region: Haiku lacks domain knowledge on HLE questions,")
    print("so it challenges what it dimly recognises and accepts what it cannot evaluate.")
    print()
    print("ANC     = Accept + No Change (final_verdict=='Accept', answer_changes==0)")
    print("HC-Cov  = P(ANC); HC-Prec = P(correct | ANC)")
    print("Gap     = HC-Prec minus Non-ANC Acc")

    # ── LaTeX output ──────────────────────────────────────────────────────────
    header_rows = [
        tex_row(
            r"\textbf{Prover}", r"\textbf{Verifier}",
            r"\textbf{Acc}", r"\textbf{HC-Cov}",
            r"\textbf{HC-Prec}", r"\textbf{Gap}",
        )
    ]
    body_rows = [
        tex_row(
            r["prover"], r["verifier"],
            tex_pct(r["acc"],     PREC["acc"]),
            tex_pct(r["hc_cov"],  PREC["hc_cov"]),
            tex_pct(r["hc_prec"], PREC["hc_prec"]),
            tex_gap(r["gap"],     PREC["gap"]),
        )
        for r in rows_data
    ]

    caption = (
        r"HLE ANC gap by model pairing. "
        r"\textbf{HC-Cov} $= \Pr[\textsc{ANC}]$; "
        r"\textbf{HC-Prec} $= \Pr[\text{correct} \mid \textsc{ANC}]$; "
        r"\textbf{Gap}: HC-Prec minus non-ANC accuracy."
    )
    tabular = build_tabular(
        col_spec="llcccc",
        header_rows=header_rows,
        body_rows=body_rows,
    )
    tex = build_table_tex(caption, "tab:hle-capability", tabular, tabcolsep=None)
    write_tex("tab_hle_capability.tex", tex)


if __name__ == "__main__":
    main()
