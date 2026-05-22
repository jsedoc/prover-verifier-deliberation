"""
Table 4 (tab:gpqa-domain) — GPQA Diamond ANC results by domain.

Writes:
    stdout                                      — ASCII preview table
    tables/generated/tab_gpqa_domain.tex        — LaTeX table environment

Precision is controlled by the PREC dict at the top of this file.

Run from worktree root:
    python tables/table4_gpqa_domain.py

Data files used:
    gpqa_results_challenge_sonnet_haiku.json  — Sonnet 4.6 + Haiku† (challenge-first)
    gpqa_results_gpt54_xhigh.json             — GPT-5.4 + GPT-5.4-mini
    gpqa_results_gemini_pro_flashlite.json    — Gemini Pro + Flash-Lite
"""

import sys
import os
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tables.utils import (
    load, is_anc_standard, print_table, fmt, fmt_gap,
    tex_pct, tex_gap, tex_row, tex_section_row,
    build_tabular, build_table_tex, write_tex, DEFAULT_PREC, paper_data_path,
)

# Footnote superscript used in this table
_DAGGER = r"\dagger"

# ── Precision control ─────────────────────────────────────────────────────────
PREC = {**DEFAULT_PREC}
# Domain-level coverage and precision: integer %
# PREC["hc_cov"] is already 0 (integer %) in DEFAULT_PREC
# domain_gap: 1 decimal (DEFAULT_PREC already has domain_gap=1)

# Domain display order (descending n)
DOMAIN_ORDER = ["Chemistry", "Physics", "Biology"]


def normalise_domain(raw: str) -> str:
    if not raw:
        return "Unknown"
    return raw.strip()


def domain_stats(data: list, anc_fn=is_anc_standard) -> dict:
    """
    Returns {domain: {n, anc_pct, prec, non_anc_acc, gap}}.
    """
    by_domain = defaultdict(list)
    for r in data:
        by_domain[normalise_domain(r.get("domain", ""))].append(r)

    results = {}
    for dom, qs in by_domain.items():
        n = len(qs)
        anc = [r for r in qs if anc_fn(r)]
        non_anc = [r for r in qs if not anc_fn(r)]
        anc_prec = 100 * sum(r["correct"] for r in anc) / len(anc) if anc else float("nan")
        non_anc_acc = 100 * sum(r["correct"] for r in non_anc) / len(non_anc) if non_anc else float("nan")
        results[dom] = dict(
            n=n,
            anc_pct=100 * len(anc) / n,
            prec=anc_prec,
            non_anc_acc=non_anc_acc,
            gap=anc_prec - non_anc_acc,
        )
    return results


def main():
    configs = [
        ("Sonnet 4.6 + Haiku†", "gpqa_results_challenge_sonnet_haiku.json", is_anc_standard),
        ("GPT-5.4 + GPT-5.4-mini", "gpqa_results_gpt54_xhigh.json", is_anc_standard),
        ("Gemini Pro + Flash-Lite", "gpqa_results_gemini_pro_flashlite.json", is_anc_standard),
    ]

    all_domain_stats = []
    for label, path, anc_fn in configs:
        data = load(paper_data_path(path))
        all_domain_stats.append((label, domain_stats(data, anc_fn)))

    # Collect domains in consistent order
    domains_in_data = set()
    for _, stats in all_domain_stats:
        domains_in_data.update(stats.keys())

    ordered_domains = [d for d in DOMAIN_ORDER if d in domains_in_data]
    ordered_domains += sorted(d for d in domains_in_data if d not in ordered_domains)

    # ── ASCII stdout ──────────────────────────────────────────────────────────
    short_headers = ["Domain", "n",
                     "HC-Cov", "HC-Prec", "Gap",
                     "HC-Cov", "HC-Prec", "Gap",
                     "HC-Cov", "HC-Prec", "Gap"]

    rows = []
    for dom in ordered_domains:
        first_stats = all_domain_stats[0][1].get(dom, {})
        row = [dom, str(first_stats.get("n", "—"))]
        for label, stats in all_domain_stats:
            s = stats.get(dom, {})
            row.append(fmt(s.get("anc_pct"), 0))
            row.append(fmt(s.get("prec"), 0))
            row.append(fmt_gap(s.get("gap", None)) + "pp")
        rows.append(row)

    print_table(short_headers, rows,
                "Table 4 — GPQA Diamond: ANC by Domain (tab:gpqa-domain)")

    print("Column groups (3 columns each after Domain/n):")
    for label, _ in all_domain_stats:
        print(f"  {label}: HC-Cov, HC-Prec, Gap")
    print()
    print("HC-Cov  = P(ANC): fraction of domain questions with ANC outcome")
    print("HC-Prec = P(correct | ANC): accuracy on ANC subset")
    print("Gap     = HC-Prec minus non-ANC accuracy")
    print()
    print("Biology n=19 (small; interpret with caution)")
    print("†: challenge-first verifier prompt")

    # ── LaTeX output ──────────────────────────────────────────────────────────
    NCOLS = 11

    header_rows = [
        # Row 1: multicolumn group labels
        (r" & & "
         r"\multicolumn{3}{c}{\textbf{Sonnet + Haiku}$^\dagger$} & "
         r"\multicolumn{3}{c}{\textbf{GPT-5.4 + GPT-5.4-mini}} & "
         r"\multicolumn{3}{c}{\textbf{Gemini Pro + Flash-Lite}} \\"),
        # Row 2: cmidrule separators (not a data row — just a rule line)
        r"\cmidrule(lr){3-5}\cmidrule(lr){6-8}\cmidrule(lr){9-11}",
        # Row 3: column labels
        tex_row(
            r"\textbf{Domain}", r"$n$",
            r"HC-Cov", r"HC-Prec", r"Gap",
            r"HC-Cov", r"HC-Prec", r"Gap",
            r"HC-Cov", r"HC-Prec", r"Gap",
        ),
    ]

    body_rows = []
    for dom in ordered_domains:
        first_stats = all_domain_stats[0][1].get(dom, {})
        n_val = first_stats.get("n", None)
        cells = [dom, str(n_val) if n_val is not None else r"\textemdash"]
        for label, stats in all_domain_stats:
            s = stats.get(dom, {})
            cells.append(tex_pct(s.get("anc_pct"), PREC["hc_cov"]))
            cells.append(tex_pct(s.get("prec"), PREC["hc_cov"]))
            cells.append(tex_gap(s.get("gap"), PREC["domain_gap"]))
        body_rows.append(tex_row(*cells))

    caption = (
        r"GPQA Diamond ANC results by domain. "
        r"\textbf{HC-Cov} $= \Pr[\textsc{ANC}]$: fraction of domain questions assigned ANC. "
        r"\textbf{HC-Prec} $= \Pr[\text{correct} \mid \textsc{ANC}]$: accuracy on ANC subset. "
        r"\textbf{Gap}: HC-Prec minus non-ANC accuracy."
    )

    tabular = build_tabular(
        col_spec="llccccccccc",
        header_rows=header_rows,
        body_rows=body_rows,
    )
    tex = build_table_tex(caption, "tab:gpqa-domain", tabular, tabcolsep="5pt")
    write_tex("tab_gpqa_domain.tex", tex)


if __name__ == "__main__":
    main()
