"""
Table 3 (tab:gpqa-main) — GPQA Diamond main results.

Writes:
    stdout                                   — ASCII preview table
    tables/generated/tab_gpqa_main.tex       — LaTeX table environment

All numbers come from tables/generated/summary.json.
Rebuild it after a result JSON changes:
    python tables/build_summary.py

Structure: grouped by prover family.
  § Sonnet 4.6  — Single-call PVD, SC, USC, Debate, Reflexion, PVD, PVD†
  § GPT-5.4     — Direct, SC*, PVD
  § Gemini 3.1 Pro  — SC*, PVD, PVD+retry

Per-row metadata (HC-signal description, footnote flags, calls-cell
override) lives in ROW_META below. Numbers do not.

Precision is controlled by the PREC dict at the top of this file.

Run from worktree root:
    python tables/table3_gpqa_main.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tables.utils import (
    print_table, fmt, fmt_gap,
    tex_pct, tex_gap, tex_row, tex_section_row,
    build_tabular, build_table_tex, write_tex, DEFAULT_PREC,
)
from tables.summary import load_summary

# Footnote superscripts
_DDAGGER = r"\ddagger"
_DAGGER  = r"\dagger"

# Precision control
PREC = {**DEFAULT_PREC}

# Column count (no Prover column — prover is the section header)
NCOLS = 8   # Method | Verifier | HC Signal | Acc | HC-Cov | HC-Prec | Gap | Calls


# ── Row metadata ──────────────────────────────────────────────────────────────
# Per-row presentation tweaks. Numbers come from summary.json by `key`.
#   hc_signal : human-readable HC signal description
#   verifier  : override summary's verifier label (e.g., "—" for direct)
#   calls_str : override the Calls cell formatting (defaults to "$\sim$N")
#   acc_flag  : footnote suffix on Acc (e.g., "*")
#   gap_flag  : footnote superscript on Gap (auto-computed: ‡ if hc_cov ≥ 90%)
#   method_override : override summary's method name for display

ROW_META = {
    # Sonnet 4.6 section
    "gpqa_single_call_sonnet":         dict(method="Single-call PVD", verifier="Self",
                                            hc_signal="ANC", calls_str="1"),
    "gpqa_sc_sonnet_k8":               dict(method="SC (k=8)",    verifier="—",
                                            hc_signal="Full consensus", calls_str="8"),
    "gpqa_usc_sonnet_k8":              dict(method="USC (k=8)",   verifier="Sonnet 4.6",
                                            hc_signal="Full consensus", calls_str="9"),
    "gpqa_debate_sonnet":              dict(method="Debate (3×2)", verifier="Sonnet 4.6",
                                            hc_signal="Agent consensus", calls_str="9"),
    "gpqa_reflexion_sonnet":           dict(method="Reflexion",   verifier="—",
                                            hc_signal="Stable, unchanged",
                                            calls_str=r"$\leq$5"),
    "gpqa_pvd_sonnet_haiku":           dict(method="PVD",         verifier="Haiku 4.5",
                                            hc_signal="ANC"),
    "gpqa_pvd_sonnet_haiku_challenge": dict(method=r"PVD$^\dagger$", verifier="Haiku 4.5",
                                            hc_signal="ANC"),
    # GPT-5.4 section
    "gpqa_direct_gpt54":               dict(method="Direct", verifier="—",
                                            hc_signal="—", calls_str="1"),
    "gpqa_sc_gpt54_k8":                dict(method=r"SC$^*$ (k=8)", verifier="—",
                                            hc_signal="Full consensus", calls_str="8"),
    "gpqa_pvd_gpt54_mini":             dict(method="PVD", verifier="GPT-5.4-mini",
                                            hc_signal="ANC"),
    # Gemini 3.1 Pro section
    "gpqa_sc_gemini_k8":               dict(method=r"SC$^*$ (k=8)", verifier="—",
                                            hc_signal="Full consensus", calls_str="8"),
    "gpqa_pvd_gemini_flashlite":       dict(method="PVD", verifier="Flash-Lite",
                                            hc_signal="ANC"),
    "gpqa_pvd_gemini_gpt55pro_retry":  dict(method="PVD+retry", verifier="GPT-5.5-pro",
                                            hc_signal="ANC"),
}

SECTIONS = [
    ("Sonnet 4.6 as prover", [
        "gpqa_single_call_sonnet",
        "gpqa_sc_sonnet_k8",
        "gpqa_usc_sonnet_k8",
        "gpqa_debate_sonnet",
        "gpqa_reflexion_sonnet",
        "gpqa_pvd_sonnet_haiku",
        "gpqa_pvd_sonnet_haiku_challenge",
    ]),
    ("GPT-5.4 as prover", [
        "gpqa_direct_gpt54",
        "gpqa_sc_gpt54_k8",
        "gpqa_pvd_gpt54_mini",
    ]),
    ("Gemini 3.1 Pro as prover", [
        "gpqa_sc_gemini_k8",
        "gpqa_pvd_gemini_flashlite",
        "gpqa_pvd_gemini_gpt55pro_retry",
    ]),
]


# ── Cell helpers ──────────────────────────────────────────────────────────────

def _calls_cell(r: dict, meta: dict) -> str:
    """Return the Calls cell. ASCII version."""
    if "calls_str" in meta:
        return meta["calls_str"]
    return f"~{r['calls']:.0f}"


def _tex_calls_cell(r: dict, meta: dict) -> str:
    """Return the Calls cell. LaTeX version."""
    if "calls_str" in meta:
        return meta["calls_str"]
    return f"$\\sim${r['calls']:.0f}"


def _gap_sup(r: dict) -> str:
    """Auto-flag ‡ if hc_cov ≥ 90 (gap estimate unreliable)."""
    cov = r.get("hc_cov")
    if cov is not None and round(cov) >= 90:
        return _DDAGGER
    return ""


def _ascii_row(r: dict, meta: dict) -> list:
    acc_flag = meta.get("acc_flag", "")
    cov  = r.get("hc_cov")
    prec = r.get("hc_prec")
    gap  = r.get("gap")
    gap_str = "—" if gap is None else fmt_gap(gap, PREC["gap"]) + "pp"
    if _gap_sup(r):
        gap_str += "‡"
    return [
        meta["method"],
        meta.get("verifier", r.get("verifier") or "—"),
        meta["hc_signal"],
        fmt(r["acc"], PREC["acc"]) + acc_flag,
        "—" if cov  is None else fmt(cov,  PREC["hc_cov"]),
        "—" if prec is None else fmt(prec, PREC["hc_prec"]),
        gap_str,
        _calls_cell(r, meta),
    ]


def _tex_row(r: dict, meta: dict) -> str:
    acc_flag = meta.get("acc_flag", "")
    cov  = r.get("hc_cov")
    prec = r.get("hc_prec")
    gap  = r.get("gap")
    return tex_row(
        meta["method"],
        meta.get("verifier", r.get("verifier") or "—"),
        meta["hc_signal"],
        (tex_pct(r["acc"], PREC["acc"]) + acc_flag) if r["acc"] is not None
            else r"\textemdash",
        tex_pct(cov,  PREC["hc_cov"])  if cov  is not None else r"\textemdash",
        tex_pct(prec, PREC["hc_prec"]) if prec is not None else r"\textemdash",
        (tex_gap(gap, PREC["gap"], superscript=_gap_sup(r))
            if gap is not None else r"\textemdash"),
        _tex_calls_cell(r, meta),
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    summary = load_summary()
    idx     = {r["key"]: r for r in summary}

    headers = ["Method", "Verifier", "HC Signal",
               "Acc", "HC-Cov", "HC-Prec", "Gap", "Calls"]

    # ASCII output
    all_rows = []
    for section, keys in SECTIONS:
        all_rows.append([f"--- {section} ---"] + [""] * (len(headers) - 1))
        for k in keys:
            r = idx[k]
            meta = ROW_META[k]
            all_rows.append(_ascii_row(r, meta))

    print_table(headers, all_rows,
                "Table 3 — GPQA Diamond: Main Results (tab:gpqa-main)")
    print("Notes:")
    print("  *   SC with extended thinking (Epoch AI benchmark); not comparable to PVD")
    print("  †   Challenge-first verifier prompt")
    print("  ‡   HC-Cov ≥90%; complement n<20; gap estimate unreliable")
    retry = idx.get("gpqa_pvd_gemini_gpt55pro_retry")
    if retry and retry["n"] < 198:
        print(f"  *   (on PVD+retry acc) Partial run ({retry['n']}/198)")
    print()
    print("HC signal: ANC (PVD) | full consensus (SC/USC) |")
    print("           agent consensus (Debate) | stable+unchanged (Reflexion)")
    print("Gap = HC-Prec minus accuracy on the complement")

    # LaTeX output
    header_rows = [
        tex_row(
            r"\textbf{Method}", r"\textbf{Verifier}", r"\textbf{HC Signal}",
            r"\textbf{Acc}", r"\textbf{HC-Cov}", r"\textbf{HC-Prec}",
            r"\textbf{Gap}", r"\textbf{Calls}",
        )
    ]
    body_rows = []
    for i, (section, keys) in enumerate(SECTIONS):
        if i > 0:
            body_rows.append(None)
        body_rows.append(tex_section_row(section, NCOLS))
        for k in keys:
            body_rows.append(_tex_row(idx[k], ROW_META[k]))

    partial_note = (f" ({retry['n']}/198)"
                    if retry and retry["n"] < 198 else "")
    caption = (
        r"GPQA Diamond results grouped by prover. "
        r"\textbf{HC-Cov}: fraction of questions flagged high-confidence. "
        r"\textbf{HC-Prec}: accuracy on that subset. "
        r"\textbf{Gap}: HC-Prec minus accuracy on the complement. "
        r"\textbf{Calls}: mean LLM calls per question. "
        r"$^*$: SC with extended thinking (Epoch AI benchmark); "
        r"not directly comparable to PVD runs (standard API). "
        r"$\dagger$: challenge-first verifier prompt. "
        r"$\ddagger$: HC coverage $>$90\%, leaving $n{<}20$ in the complement; "
        r"gap estimate unreliable. "
        + (f"*: partial run{partial_note} (PVD+retry accuracy)." if partial_note else "")
    )

    tabular = build_tabular(
        col_spec="lllccccc",
        header_rows=header_rows,
        body_rows=body_rows,
    )
    tex = build_table_tex(caption, "tab:gpqa-main", tabular, tabcolsep="3pt")
    write_tex("tab_gpqa_main.tex", tex)


if __name__ == "__main__":
    main()
