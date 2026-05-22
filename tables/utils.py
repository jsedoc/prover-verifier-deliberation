"""
Shared utilities for table reproduction scripts.
All scripts should be run from the worktree root:
    cd /path/to/trust_but_verify_decoding/.claude/worktrees/ecstatic-banzai
    python tables/table3_gpqa_main.py

Generated .tex files are written to the repo root's tables/generated/ directory
so that neurips_paper.tex can use \\input{tables/generated/tab_xxx.tex} for them.
"""

import json
import os
from typing import Optional

# ── Paths ─────────────────────────────────────────────────────────────────────

# Worktree root = one level above this file's directory (tables/)
_TABLES_DIR  = os.path.dirname(os.path.abspath(__file__))
_WORKTREE    = os.path.dirname(_TABLES_DIR)

def _find_repo_root(start: str) -> str:
    """Walk up from start until we find a directory containing neurips_paper.tex."""
    d = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(d, "neurips_paper.tex")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            # Fallback: assume we're already at the repo root
            return start
        d = parent

_REPO_ROOT   = _find_repo_root(_WORKTREE)
TEX_DIR      = os.path.join(_REPO_ROOT, "tables", "generated")
PAPER_DATA_DIR = os.path.join(_REPO_ROOT, "data", "paper")


def paper_data_path(filename: str) -> str:
    """Return the path to a legacy-format raw result file used by paper tables."""
    return os.path.join(PAPER_DATA_DIR, filename)


# ── Data loading ──────────────────────────────────────────────────────────────

def load(path: str) -> list:
    """
    Load a JSON results file (list or dict of results).

    Filters out any rttr-v1 `_meta` header record so consumers that just
    iterate ``r["correct"]`` etc. keep working when pointed at a new file.
    """
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = list(data.values())
    # Drop the rttr-v1 _meta header record if present
    return [r for r in data if not (isinstance(r, dict) and r.get("_meta"))]


# ── ANC detection ─────────────────────────────────────────────────────────────

def is_anc_standard(r: dict) -> bool:
    """ANC for single-attempt PVD files: final_verdict=='Accept' AND answer_changes==0."""
    return (r.get("final_verdict") == "Accept" and
            r.get("answer_changes", 0) == 0)


def is_anc_retry(r: dict) -> bool:
    """ANC for retry-protocol files (have 'outcome' field): outcome=='accept_no_change'."""
    return r.get("outcome") == "accept_no_change"


# ── Metric computation ────────────────────────────────────────────────────────

def pvd_stats(data: list, anc_fn=is_anc_standard) -> dict:
    """
    Compute standard PVD selective-prediction metrics.

    Returns:
        n, acc, hc, non_hc, hc_cov, hc_prec, non_hc_acc, gap, avg_rounds
    """
    n = len(data)
    hc = [r for r in data if anc_fn(r)]
    non_hc = [r for r in data if not anc_fn(r)]

    acc        = 100 * sum(r["correct"] for r in data) / n
    hc_prec    = 100 * sum(r["correct"] for r in hc)     / len(hc)    if hc     else float("nan")
    non_hc_acc = 100 * sum(r["correct"] for r in non_hc) / len(non_hc) if non_hc else float("nan")
    gap        = hc_prec - non_hc_acc

    rounds_key = "total_rounds" if "total_rounds" in data[0] else "rounds_used"
    avg_rounds = sum(r.get(rounds_key, 0) for r in data) / n

    return dict(
        n=n, acc=acc,
        hc=hc, non_hc=non_hc,
        hc_cov=100 * len(hc) / n,
        hc_prec=hc_prec, non_hc_acc=non_hc_acc,
        gap=gap,
        avg_rounds=avg_rounds,
    )


def pvd_calls(avg_rounds: float, avg_attempts: float = 1.0) -> float:
    """
    Estimate mean LLM calls per question.
    Formula: 2 * avg_rounds, because `avg_rounds` counts verifier turns
    and each attempt has one initial prover call but one fewer prover
    follow-up than verifier turn.
    """
    return 2 * avg_rounds


# ── Precision control ─────────────────────────────────────────────────────────
#
# Each table script defines its own PREC dict:
#   PREC = {"acc": 1, "hc_cov": 0, "hc_prec": 1, "gap": 1, ...}
# Then passes it to fmt() / tex_fmt() so all rounding is controlled in one place.
#
# Default precision (used when a table script doesn't override):
DEFAULT_PREC = {
    "acc":     1,   # overall accuracy:   83.3%
    "hc_cov":  0,   # HC coverage:        72%   (integer)
    "hc_prec": 1,   # HC precision:       91.5%
    "gap":     1,   # gap in pp:          +29.0
    "rounds":  1,   # avg rounds:         2.1
    "n_pct":   0,   # n / percentage:     54%   (integer)
    "domain_prec": 0,  # domain precision:  80%  (integer)
    "domain_gap":  1,  # domain gap:        +25.1
}


# ── ASCII pretty-print helpers ────────────────────────────────────────────────

def fmt(val: Optional[float], decimals: int = 1, suffix: str = "%") -> str:
    if val is None or val != val:
        return "—"
    return f"{val:.{decimals}f}{suffix}"


def fmt_gap(val: Optional[float], decimals: int = 1) -> str:
    if val is None or val != val:
        return "—"
    return f"{val:+.{decimals}f}"


def hline(widths: list) -> str:
    return "+-" + "-+-".join("-" * w for w in widths) + "-+"


def _row_ascii(cells: list, widths: list) -> str:
    parts = [f" {str(c):<{w}} " for c, w in zip(cells, widths)]
    return "|" + "|".join(parts) + "|"


def print_table(headers: list, rows: list, title: str = ""):
    if title:
        print(f"\n{'─'*60}")
        print(f"  {title}")
        print(f"{'─'*60}")
    widths = [max(len(str(h)), max((len(str(r[i])) for r in rows), default=0))
              for i, h in enumerate(headers)]
    print(hline(widths))
    print(_row_ascii(headers, widths))
    print(hline(widths))
    for r_ in rows:
        print(_row_ascii(r_, widths))
    print(hline(widths))
    print()


# ── LaTeX formatting helpers ──────────────────────────────────────────────────

def tex_pct(val: Optional[float], decimals: int = 1,
            bold: bool = False, flag: str = "") -> str:
    """Format a percentage for LaTeX: '83.3\\%' or '72\\%'."""
    if val is None or val != val:
        return r"\textemdash"
    s = f"{val:.{decimals}f}\\%"
    if flag:
        s = s + flag          # e.g. '*' appended after the %
    if bold:
        s = r"\textbf{" + s + "}"
    return s


def tex_gap(val: Optional[float], decimals: int = 1,
            bold: bool = False, superscript: str = "") -> str:
    """
    Format a gap as '$+29.0$' or '$+52.4^{\\ddagger}$'.
    superscript: full LaTeX inside ^{...}, e.g. r'\\ddagger' or r'\\dagger'.
    """
    if val is None or val != val:
        return r"\textemdash"
    sign = "+" if val >= 0 else ""
    inner = f"{sign}{val:.{decimals}f}"
    if superscript:
        inner += f"^{{{superscript}}}"
    s = f"${inner}$"
    if bold:
        s = f"$\\mathbf{{{inner}}}$"
    return s


def tex_num(val: Optional[float], decimals: int = 1,
            prefix: str = "", suffix: str = "", bold: bool = False) -> str:
    """Format a plain number for LaTeX (no percent sign)."""
    if val is None or val != val:
        return r"\textemdash"
    s = f"{prefix}{val:.{decimals}f}{suffix}"
    return r"\textbf{" + s + "}" if bold else s


def tex_calls(val: Optional[float], decimals: int = 0, special: str = "") -> str:
    """Format Calls column: '~3' or '≤5' or custom."""
    if special:
        return special
    if val is None or val != val:
        return r"\textemdash"
    return f"$\\sim${val:.{decimals}f}"


# ── LaTeX table builder ───────────────────────────────────────────────────────

def build_tabular(col_spec: str, header_rows: list, body_rows: list,
                  footer_rows: list = None) -> str:
    """
    Build a complete LaTeX tabular environment.

    header_rows : list of strings (already-formatted LaTeX row lines including \\\\)
    body_rows   : list of strings, None entries become \\midrule
    footer_rows : list of strings appended before \\bottomrule (optional)
    """
    lines = [
        r"\begin{tabular}{" + col_spec + "}",
        r"\toprule",
    ]
    for hr in header_rows:
        lines.append(hr)
    lines.append(r"\midrule")
    for br in body_rows:
        if br is None:
            lines.append(r"\midrule")
        else:
            lines.append(br)
    if footer_rows:
        lines.append(r"\midrule")
        for fr in footer_rows:
            lines.append(fr)
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


def build_table_tex(caption: str, label: str, tabular: str,
                    placement: str = "h",
                    small: bool = True,
                    tabcolsep: Optional[str] = "4pt") -> str:
    """
    Wrap a tabular block in a full LaTeX table float.
    """
    lines = [
        f"\\begin{{table}}[{placement}]",
        r"\centering",
        f"\\caption{{{caption}}}",
        f"\\label{{{label}}}",
    ]
    if small:
        lines.append(r"\small")
    if tabcolsep:
        lines.append(f"\\setlength{{\\tabcolsep}}{{{tabcolsep}}}")
    lines.append(tabular)
    lines.append(r"\end{table}")
    return "\n".join(lines)


def write_tex(filename: str, content: str) -> None:
    """Write content to tables/generated/<filename> in the repo root."""
    os.makedirs(TEX_DIR, exist_ok=True)
    path = os.path.join(TEX_DIR, filename)
    with open(path, "w") as f:
        f.write(content)
        f.write("\n")  # trailing newline
    print(f"  → {path}")


def tex_row(*cells, end: str = r"\\") -> str:
    """Join cells with ' & ' and append the row terminator."""
    return " & ".join(str(c) for c in cells) + " " + end


def tex_section_row(text: str, ncols: int) -> str:
    """A multicolumn section header row like \\textit{Baselines}."""
    return f"\\multicolumn{{{ncols}}}{{l}}{{\\textit{{{text}}}}} \\\\"
