"""
Table 6 (tab:verifier) — Effect of verifier choice on ANC calibration.

Writes:
    stdout                                  — ASCII preview table
    tables/generated/tab_verifier.tex       — LaTeX table environment

Precision is controlled by the PREC dict at the top of this file.

Run from worktree root:
    python tables/table6_verifier.py

Data files used:
    gpqa_results_single_call_sonnet.json          — Sonnet 4.6 / Self (single-call ablation)
    gpqa_results_diamond_full.json                — Sonnet 4.6 / Haiku
    gpqa_results_challenge_sonnet_haiku.json      — Sonnet 4.6 / Haiku† (challenge-first)
    gpqa_results_gemini_pro_flashlite.json        — Gemini Pro / Flash-Lite
    gpqa_results_gemini_3.1_pro__gpt_5.5_pro_retry.json  — Gemini Pro / GPT-5.5-pro (retry)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tables.utils import (
    load, pvd_stats, is_anc_standard, is_anc_retry, print_table, fmt, fmt_gap,
    tex_pct, tex_gap, tex_num, tex_row, tex_section_row,
    build_tabular, build_table_tex, write_tex, DEFAULT_PREC, paper_data_path,
)

# Footnote superscript used in this table
_DAGGER = r"\dagger"

# ── Precision control ─────────────────────────────────────────────────────────
PREC = {**DEFAULT_PREC}
# ANC%: integer % → hc_cov (0 decimals)
# ANC Prec, Non-ANC Acc: 1 decimal → hc_prec (1 decimal)
# Gap: 1 decimal → gap (1 decimal)
# Avg R: 1 decimal → rounds (1 decimal)


def config_row(label_prover, label_verifier, data, anc_fn):
    s = pvd_stats(data, anc_fn=anc_fn)
    return dict(
        prover=label_prover,
        verifier=label_verifier,
        anc_pct=s["hc_cov"],
        anc_prec=s["hc_prec"],
        non_anc_acc=s["non_hc_acc"],
        gap=s["gap"],
        avg_rounds=s["avg_rounds"],
        n=s["n"],
    )


def main():
    # ── Sonnet 4.6 prover ────────────────────────────────────────────────────
    self_data = load(paper_data_path("gpqa_results_single_call_sonnet.json"))
    self_row = config_row("Sonnet 4.6", r"\emph{Self}", self_data, is_anc_standard)

    haiku_data = load(paper_data_path("gpqa_results_diamond_full.json"))
    haiku_row = config_row(
        "Sonnet 4.6", "Haiku",
        haiku_data,
        anc_fn=lambda r: r.get("final_verdict") == "Accept",
    )

    haiku_cf_data = load(paper_data_path("gpqa_results_challenge_sonnet_haiku.json"))
    haiku_cf_row = config_row("Sonnet 4.6", r"Haiku$^\dagger$", haiku_cf_data, is_anc_standard)

    # ── Gemini 3.1 Pro prover ─────────────────────────────────────────────────
    flashlite_data = load(paper_data_path("gpqa_results_gemini_pro_flashlite.json"))
    flashlite_row = config_row("Gemini Pro", "Flash-Lite", flashlite_data, is_anc_standard)

    retry_data = load(paper_data_path("gpqa_results_gemini_3.1_pro__gpt_5.5_pro_retry.json"))
    retry_row = config_row(
        "Gemini Pro", "GPT-5.5-pro",
        retry_data, is_anc_retry,
    )

    sections = [
        ("Sonnet 4.6 prover", [self_row, haiku_row, haiku_cf_row]),
        ("Gemini 3.1 Pro prover", [flashlite_row, retry_row]),
    ]

    # ── ASCII stdout ──────────────────────────────────────────────────────────
    headers = ["Prover", "Verifier", "HC-Cov", "HC-Prec", "Non-ANC Acc", "Gap", "Avg R"]

    # ASCII verifier labels (no LaTeX)
    ascii_verifier = {
        r"\emph{Self}":      "Self (ablation)",
        "Haiku":             "Haiku",
        r"Haiku$^\dagger$":  "Haiku†",
        "Flash-Lite":        "Flash-Lite",
        "GPT-5.5-pro":       "GPT-5.5-pro",
    }

    rows = []
    for section, cfgs in sections:
        rows.append([f"--- {section} ---"] + [""] * (len(headers) - 1))
        for c in cfgs:
            partial = f"*  (n={c['n']}/198)" if c["n"] < 198 else ""
            verifier_label = ascii_verifier.get(c["verifier"], c["verifier"])
            rows.append([
                c["prover"],
                verifier_label + (" " + partial if partial else ""),
                fmt(c["anc_pct"], 0),
                fmt(c["anc_prec"], 1),
                fmt(c["non_anc_acc"], 1),
                fmt_gap(c["gap"]) + "pp",
                f"{c['avg_rounds']:.1f}",
            ])

    print_table(headers, rows,
                "Table 6 — Verifier Choice Effect on ANC Calibration (tab:verifier)")

    print("†  Challenge-first verifier prompt")
    print("*  Partial run; n shown inline")
    print()
    print("Avg R   = mean challenge rounds per question")
    print("          (for retry: avg total rounds across all attempts)")
    print("HC-Cov  = P(ANC); HC-Prec = P(correct | ANC)")
    print("Gap     = HC-Prec minus Non-ANC Acc")
    print()
    print("Self (ablation): one model call acts as both prover and verifier.")

    # ── LaTeX output ──────────────────────────────────────────────────────────
    NCOLS = 7

    header_rows = [
        tex_row(
            r"\textbf{Prover}", r"\textbf{Verifier}",
            r"\textbf{HC-Cov}", r"\textbf{HC-Prec}", r"\textbf{Non-ANC Acc}",
            r"\textbf{Gap}", r"\textbf{Avg R}",
        )
    ]

    body_rows = []
    for i, (section, cfgs) in enumerate(sections):
        if i > 0:
            body_rows.append(None)   # → \midrule between sections
        body_rows.append(tex_section_row(section, NCOLS))
        for c in cfgs:
            body_rows.append(tex_row(
                c["prover"],
                c["verifier"],
                tex_pct(c["anc_pct"], PREC["hc_cov"]),
                tex_pct(c["anc_prec"], PREC["hc_prec"]),
                tex_pct(c["non_anc_acc"], PREC["hc_prec"]),
                tex_gap(c["gap"], PREC["gap"]),
                tex_num(c["avg_rounds"], PREC["rounds"]),
            ))

    caption = (
        r"Effect of verifier choice on ANC calibration, holding prover fixed. "
        r"\textbf{Avg R}: mean challenge rounds. "
        r"$\dagger$: challenge-first prompt. "
        r"\emph{Self}: same model as prover (single-call ablation)."
    )

    tabular = build_tabular(
        col_spec="llccccc",
        header_rows=header_rows,
        body_rows=body_rows,
    )
    tex = build_table_tex(caption, "tab:verifier", tabular, tabcolsep="5pt")
    write_tex("tab_verifier.tex", tex)


if __name__ == "__main__":
    main()
