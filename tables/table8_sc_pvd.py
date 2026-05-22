"""
Table 8 (tab:sc-pvd) — Overlap between SC full-consensus and PVD ANC on GPQA Diamond.

Writes:
    stdout                                  — ASCII preview table
    tables/generated/tab_sc_pvd.tex         — LaTeX table environment

Precision is controlled by the PREC dict at the top of this file.

Run from worktree root:
    python tables/table8_sc_pvd.py

Data files used:
    gpqa_results_usc.json                      — SC results (sc_correct, agreement_rate)
    gpqa_results_challenge_sonnet_haiku.json   — PVD results (challenge-first, Haiku verifier)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tables.utils import (
    load, is_anc_standard, print_table, fmt, fmt_gap,
    tex_pct, tex_row,
    build_tabular, build_table_tex, write_tex, DEFAULT_PREC, paper_data_path,
)

# ── Precision control ─────────────────────────────────────────────────────────
PREC = {**DEFAULT_PREC}
# % column: integer → hc_cov (0 decimals)
# SC Acc and PVD Acc: 1 decimal → hc_prec (1 decimal)


def main():
    # Load both result sets, keyed by question_num for alignment
    sc_data  = {r["question_num"]: r for r in load(paper_data_path("gpqa_results_usc.json"))}
    pvd_data = {r["question_num"]: r for r in load(paper_data_path("gpqa_results_challenge_sonnet_haiku.json"))}

    common_qnums = sorted(set(sc_data) & set(pvd_data))
    n = len(common_qnums)

    both_hc  = []
    sc_only  = []
    pvd_only = []
    neither  = []

    for qn in common_qnums:
        s = sc_data[qn]
        p = pvd_data[qn]

        sc_hc   = s["agreement_rate"] == 1.0
        pvd_anc = is_anc_standard(p)

        entry = (s, p)
        if sc_hc and pvd_anc:
            both_hc.append(entry)
        elif sc_hc and not pvd_anc:
            sc_only.append(entry)
        elif not sc_hc and pvd_anc:
            pvd_only.append(entry)
        else:
            neither.append(entry)

    def acc(grp, key):
        if not grp:
            return float("nan")
        return 100 * sum(s[key] for s, p in grp) / len(grp)

    categories = [
        ("Both HC",  both_hc),
        ("SC only",  sc_only),
        ("PVD only", pvd_only),
        ("Neither",  neither),
    ]

    headers = ["Category", "n", "%", "SC Acc", "PVD Acc"]

    # ── ASCII stdout ──────────────────────────────────────────────────────────
    rows = []
    for cat, grp in categories:
        sc_acc_val  = acc(grp, "sc_correct")
        pvd_acc_val = 100 * sum(p["correct"] for s, p in grp) / len(grp) if grp else float("nan")
        rows.append([
            cat,
            str(len(grp)),
            fmt(100 * len(grp) / n, 0),
            fmt(sc_acc_val, 1),
            fmt(pvd_acc_val, 1),
        ])

    print_table(headers, rows,
                "Table 8 — SC vs PVD Overlap on GPQA Diamond (tab:sc-pvd)")

    # ── Error complementarity analysis (matches paper §6.3) ─────────────────
    sc_confident_wrong = (
        [(s, p, "sc_only")  for s, p in sc_only  if not s["sc_correct"]] +
        [(s, p, "both_hc")  for s, p in both_hc  if not s["sc_correct"]]
    )
    pvd_rejected_sc_wrong = sum(1 for s, p, cell in sc_confident_wrong if cell == "sc_only")

    pvd_accepted_wrong = (
        [(s, p, "pvd_only") for s, p in pvd_only if not p["correct"]] +
        [(s, p, "both_hc")  for s, p in both_hc  if not p["correct"]]
    )
    sc_flagged_pvd_wrong = sum(1 for s, p, cell in pvd_accepted_wrong if cell == "pvd_only")

    total_sc_wrong  = len(sc_confident_wrong)
    total_pvd_wrong = len(pvd_accepted_wrong)

    print("Error complementarity (from §Discussion):")
    print(f"  SC confident yet wrong: {total_sc_wrong} questions")
    print(f"    PVD rejected {pvd_rejected_sc_wrong}/{total_sc_wrong} "
          f"({100*pvd_rejected_sc_wrong/total_sc_wrong:.0f}%) of these  "
          f"← PVD catches SC's overconfident errors")
    print()
    print(f"  PVD accepted yet wrong: {total_pvd_wrong} questions")
    print(f"    SC flagged non-consensus on {sc_flagged_pvd_wrong}/{total_pvd_wrong} "
          f"({100*sc_flagged_pvd_wrong/total_pvd_wrong:.0f}%)  "
          f"← SC catches PVD's false-ANCs")
    print()
    print("SC  = Self-Consistency (k=8, Sonnet 4.6); HC signal = full consensus (agreement_rate=1.0)")
    print("PVD = challenge-first Haiku verifier (Sonnet 4.6 prover); HC signal = ANC")

    # ── LaTeX output ──────────────────────────────────────────────────────────
    header_rows = [
        tex_row(
            r"",
            r"\textbf{$n$}",
            r"\textbf{\%}",
            r"\textbf{SC Acc}",
            r"\textbf{PVD Acc}",
        )
    ]

    body_rows = []
    for cat, grp in categories:
        sc_acc_val  = acc(grp, "sc_correct")
        pvd_acc_val = 100 * sum(p["correct"] for s, p in grp) / len(grp) if grp else float("nan")
        pct_val = 100 * len(grp) / n
        body_rows.append(tex_row(
            cat,
            str(len(grp)),               # n: plain integer
            tex_pct(pct_val, PREC["hc_cov"]),    # %: integer
            tex_pct(sc_acc_val, PREC["hc_prec"]),   # SC Acc: 1 decimal
            tex_pct(pvd_acc_val, PREC["hc_prec"]),  # PVD Acc: 1 decimal
        ))

    caption = (
        r"Overlap between SC full-consensus and PVD ANC on GPQA Diamond ($n{=}198$). "
        r"Accuracy columns report correctness within each cell."
    )

    tabular = build_tabular(
        col_spec="lcccc",
        header_rows=header_rows,
        body_rows=body_rows,
    )
    # tabcolsep not set for this table (None → omit \setlength)
    tex = build_table_tex(caption, "tab:sc-pvd", tabular, tabcolsep=None)
    write_tex("tab_sc_pvd.tex", tex)


if __name__ == "__main__":
    main()
