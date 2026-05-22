"""
Table 5 (tab:hle) — Humanity's Last Exam results by domain.

Writes:
    stdout                                  — ASCII preview table
    tables/generated/tab_hle.tex            — LaTeX table environment

Precision is controlled by the PREC dict at the top of this file.

Run from worktree root:
    python tables/table5_hle_domain.py

Data file used:
    hle_results_gpt_5.5__gemini_3.1_pro.json
"""

import sys
import os
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tables.utils import (
    load, is_anc_standard, print_table, fmt, fmt_gap,
    tex_pct, tex_gap, tex_row,
    build_tabular, build_table_tex, write_tex, DEFAULT_PREC, paper_data_path,
)

# ── Precision control ─────────────────────────────────────────────────────────
PREC = {**DEFAULT_PREC}
# Overall Acc and ANC%: integer %  → use hc_cov (0 decimals)
# ANC Prec and Non-ANC Acc: 1 decimal → use hc_prec (1 decimal)
# Gap: 1 decimal → use gap (1 decimal)

# Start with the largest categories, then append smaller categories so the
# generated table and "All" total expose the full HLE run.
PAPER_DOMAINS = [
    "Biology/Medicine",
    "Math",
    "Humanities/Social Science",
    "Computer Science/AI",
    "Other",
    "Physics",
]

DISPLAY_NAMES = {
    "Biology/Medicine":          "Biology / Medicine",
    "Math":                      "Math",
    "Humanities/Social Science": r"Humanities / Soc.\ Sci.",
    "Computer Science/AI":       "Computer Science / AI",
    "Other":                     "Other",
    "Physics":                   "Physics",
    "Chemistry":                 "Chemistry",
    "Engineering":               "Engineering",
}

# Display name for ASCII stdout (no LaTeX escapes)
ASCII_DISPLAY_NAMES = {
    "Biology/Medicine":          "Biology / Medicine",
    "Math":                      "Math",
    "Humanities/Social Science": "Humanities / Soc. Sci.",
    "Computer Science/AI":       "Computer Science / AI",
    "Other":                     "Other",
    "Physics":                   "Physics",
    "Chemistry":                 "Chemistry",
    "Engineering":               "Engineering",
}


def domain_stats(data: list) -> dict:
    by_cat = defaultdict(list)
    for r in data:
        by_cat[r["category"]].append(r)

    out = {}
    for cat, qs in by_cat.items():
        n = len(qs)
        anc = [r for r in qs if is_anc_standard(r)]
        non_anc = [r for r in qs if not is_anc_standard(r)]
        acc = 100 * sum(r["correct"] for r in qs) / n
        anc_prec = 100 * sum(r["correct"] for r in anc) / len(anc) if anc else float("nan")
        non_anc_acc = (100 * sum(r["correct"] for r in non_anc) / len(non_anc)
                       if non_anc else float("nan"))
        out[cat] = dict(
            n=n, acc=acc,
            anc_pct=100 * len(anc) / n,
            anc_prec=anc_prec,
            non_anc_acc=non_anc_acc,
            gap=anc_prec - non_anc_acc,
        )
    return out


def main():
    data = load(paper_data_path("hle_results_gpt_5.5__gemini_3.1_pro.json"))
    stats = domain_stats(data)

    # Overall stats
    n_all = len(data)
    anc_all = [r for r in data if is_anc_standard(r)]
    non_anc_all = [r for r in data if not is_anc_standard(r)]
    acc_all = 100 * sum(r["correct"] for r in data) / n_all
    anc_pct_all = 100 * len(anc_all) / n_all
    anc_prec_all = 100 * sum(r["correct"] for r in anc_all) / len(anc_all)
    non_anc_acc_all = 100 * sum(r["correct"] for r in non_anc_all) / len(non_anc_all)
    gap_all = anc_prec_all - non_anc_acc_all

    # Sort by n descending (as in paper); only paper domains
    domain_order = [d for d in PAPER_DOMAINS if d in stats]
    # Append any remaining domains not in PAPER_DOMAINS
    domain_order += sorted(
        (d for d in stats if d not in PAPER_DOMAINS),
        key=lambda d: -stats[d]["n"]
    )

    headers = ["Domain", "n", "Overall Acc", "HC-Cov", "HC-Prec", "Non-ANC Acc", "Gap"]

    # ── ASCII stdout ──────────────────────────────────────────────────────────
    rows = []
    for cat in domain_order:
        s = stats[cat]
        display = ASCII_DISPLAY_NAMES.get(cat, cat)
        rows.append([
            display,
            str(s["n"]),
            fmt(s["acc"], 0),
            fmt(s["anc_pct"], 0),
            fmt(s["anc_prec"], 0),
            fmt(s["non_anc_acc"], 0),
            fmt_gap(s["gap"]) + "pp",
        ])

    rows.append(["---"] * len(headers))
    rows.append([
        "All",
        str(n_all),
        fmt(acc_all, 1),
        fmt(anc_pct_all, 0),
        fmt(anc_prec_all, 1),
        fmt(non_anc_acc_all, 1),
        fmt_gap(gap_all) + "pp",
    ])

    print_table(headers, rows,
                "Table 5 — Humanity's Last Exam: ANC by Domain (tab:hle)")

    print("Configuration: GPT-5.5 prover / Gemini 3.1 Pro verifier, T=12, K=1")
    print()
    print("Note: categories are ordered by the paper's primary grouping, with")
    print("  smaller categories appended before the 'All' total.")
    print()
    print("ANC     = Accept + No Change (final_verdict=='Accept' and answer_changes==0)")
    print("HC-Cov  = P(ANC); HC-Prec = P(correct | ANC)")
    print("Gap     = HC-Prec minus Non-ANC Acc")

    # ── LaTeX output ──────────────────────────────────────────────────────────
    header_rows = [
        tex_row(
            r"\textbf{Domain}", r"$n$",
            r"\textbf{Overall Acc}", r"\textbf{HC-Cov}",
            r"\textbf{HC-Prec}", r"\textbf{Non-ANC Acc}", r"\textbf{Gap}",
        )
    ]

    body_rows = []
    for cat in domain_order:
        s = stats[cat]
        display = DISPLAY_NAMES.get(cat, cat)
        body_rows.append(tex_row(
            display,
            str(s["n"]),
            tex_pct(s["acc"], PREC["hc_cov"]),        # Overall Acc: integer %
            tex_pct(s["anc_pct"], PREC["hc_cov"]),    # ANC%: integer %
            tex_pct(s["anc_prec"], PREC["hc_prec"]),  # ANC Prec: 1 decimal
            tex_pct(s["non_anc_acc"], PREC["hc_prec"]),  # Non-ANC Acc: 1 decimal
            tex_gap(s["gap"], PREC["gap"]),            # Gap: 1 decimal
        ))

    # "All" footer row — bold
    footer_rows = [
        tex_row(
            r"\textbf{All}",
            r"\textbf{" + str(n_all) + r"}",
            r"\textbf{" + tex_pct(acc_all, PREC["acc"]) + r"}",
            r"\textbf{" + tex_pct(anc_pct_all, PREC["hc_cov"]) + r"}",
            r"\textbf{" + tex_pct(anc_prec_all, PREC["hc_prec"]) + r"}",
            r"\textbf{" + tex_pct(non_anc_acc_all, PREC["hc_prec"]) + r"}",
            r"$\mathbf{" + f"{gap_all:+.{PREC['gap']}f}" + r"}$",
        )
    ]

    caption = (
        r"Humanity's Last Exam results "
        r"(GPT-5.5 prover, Gemini 3.1 Pro verifier, $n{=}513$, $T{=}12$, $K{=}1$). "
        r"Rows sorted by $n$. \textbf{HC-Cov} $= \Pr[\textsc{ANC}]$; "
        r"\textbf{HC-Prec} $= \Pr[\text{correct} \mid \textsc{ANC}]$; "
        r"\textbf{Gap}: HC-Prec minus non-ANC accuracy."
    )

    tabular = build_tabular(
        col_spec="lcccccc",
        header_rows=header_rows,
        body_rows=body_rows,
        footer_rows=footer_rows,
    )
    tex = build_table_tex(caption, "tab:hle", tabular, tabcolsep="5pt")
    write_tex("tab_hle.tex", tex)


if __name__ == "__main__":
    main()
