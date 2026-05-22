"""
Build a single summary.json that consolidates all per-method metrics used
by tables and figures. Run this whenever a result JSON changes:

    python tables/build_summary.py

Writes:
    tables/generated/summary.json

The schema is a list of "run" dicts. Each run has the following keys:

    key             : short unique identifier
    display_name    : label for plots/tables (may contain LaTeX)
    method_family   : "baseline" | "pvd_sonnet" | "pvd_cross_family"
    method          : pretty method name (e.g., "PVD+retry", "Single-call PVD")
    prover          : prover model display name
    verifier        : verifier model display name or None
    dataset         : "GPQA Diamond" | "HLE"
    source_json     : path to raw results file

    # Selective-prediction metrics
    n               : number of questions evaluated
    acc             : overall accuracy (%)
    hc_cov          : HC coverage (%)
    hc_prec         : HC precision (%)
    non_hc_acc      : accuracy on the complement (%)
    gap             : hc_prec - non_hc_acc (pp)
    avg_rounds      : mean PVD rounds per question (NaN if N/A)
    avg_attempts    : mean PVD retry attempts per question
    calls           : mean LLM calls per question

    # Efficiency metrics
    eff_source      : "logged"  -> tokens/cost taken directly from the
                                   result file's per-question logs
                      "modeled" -> tokens/cost are the coarse estimate from
                                   cost_model.py (the file carries no logs)
    tokens_in       : per-question input tokens  (logged or modeled per eff_source)
    tokens_out      : per-question output tokens (logged or modeled per eff_source)
    tokens_total    : per-question total tokens  (logged or modeled per eff_source)
    cost_usd        : per-question USD cost       (logged or modeled per eff_source)
    calls           : mean LLM calls per question (always an exact structural
                      count, independent of eff_source)

Rule of thumb: whenever a result file carries per-question token + cost
logs, we report those measured numbers; the cost_model.py estimate is only
a fallback for files that predate token logging. The eff_source field makes
this explicit so a modeled estimate is never mistaken for measured data.

This is the ONLY place hardcoded mapping between raw files and
display names lives. Update here, rebuild, and everything propagates.
"""

import json
import math
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tables.utils import (
    load, pvd_stats, is_anc_standard, is_anc_retry, TEX_DIR, _REPO_ROOT,
    paper_data_path,
)
from tables.cost_model import (
    tokens_and_cost_pvd, tokens_and_cost_direct, tokens_and_cost_sc,
    tokens_and_cost_debate,
)

SUMMARY_PATH = os.path.join(TEX_DIR, "summary.json")


# ── Helpers for non-PVD methods ──────────────────────────────────────────────

def _direct_metrics(data: list) -> dict:
    n = len(data)
    acc = 100 * sum(r["correct"] for r in data) / n
    return dict(n=n, acc=acc,
                hc_cov=None, hc_prec=None, non_hc_acc=None, gap=None,
                avg_rounds=float("nan"), avg_attempts=1.0)


def _sc_usc_metrics(data: list) -> tuple:
    """Returns (sc_dict, usc_dict) from the shared USC results file."""
    n = len(data)
    hc     = [r for r in data if r["agreement_rate"] == 1.0]
    non_hc = [r for r in data if r["agreement_rate"] < 1.0]

    sc_acc        = 100 * sum(r["sc_correct"]  for r in data) / n
    sc_hc_prec    = 100 * sum(r["sc_correct"]  for r in hc)     / len(hc)    if hc else float("nan")
    sc_non_hc_acc = 100 * sum(r["sc_correct"]  for r in non_hc) / len(non_hc) if non_hc else float("nan")

    usc_acc        = 100 * sum(r["usc_correct"] for r in data) / n
    usc_hc_prec    = 100 * sum(r["usc_correct"] for r in hc)   / len(hc)     if hc else float("nan")
    usc_non_hc_acc = sc_non_hc_acc

    hc_cov = 100 * len(hc) / n
    return (
        dict(n=n, acc=sc_acc, hc_cov=hc_cov, hc_prec=sc_hc_prec,
             non_hc_acc=sc_non_hc_acc, gap=sc_hc_prec - sc_non_hc_acc,
             avg_rounds=float("nan"), avg_attempts=1.0),
        dict(n=n, acc=usc_acc, hc_cov=hc_cov, hc_prec=usc_hc_prec,
             non_hc_acc=usc_non_hc_acc, gap=usc_hc_prec - usc_non_hc_acc,
             avg_rounds=float("nan"), avg_attempts=1.0),
    )


def _debate_metrics(data: list) -> dict:
    n = len(data)
    acc = 100 * sum(r["correct"] for r in data) / n
    hc     = [r for r in data if r["consensus_reached"]]
    non_hc = [r for r in data if not r["consensus_reached"]]
    hc_prec    = 100 * sum(r["correct"] for r in hc)     / len(hc)     if hc else float("nan")
    non_hc_acc = 100 * sum(r["correct"] for r in non_hc) / len(non_hc) if non_hc else float("nan")
    return dict(n=n, acc=acc, hc_cov=100 * len(hc) / n,
                hc_prec=hc_prec, non_hc_acc=non_hc_acc,
                gap=hc_prec - non_hc_acc,
                avg_rounds=float("nan"), avg_attempts=1.0,
                n_agents=data[0].get("num_agents", 3),
                n_rounds=data[0].get("num_rounds", 2))


def _reflexion_metrics(data: list) -> dict:
    n = len(data)
    acc = 100 * sum(r["correct"] for r in data) / n
    hc     = [r for r in data if r.get("final_stable") and not r.get("answer_changed")]
    non_hc = [r for r in data if not (r.get("final_stable") and not r.get("answer_changed"))]
    hc_prec    = 100 * sum(r["correct"] for r in hc)     / len(hc)     if hc else float("nan")
    non_hc_acc = 100 * sum(r["correct"] for r in non_hc) / len(non_hc) if non_hc else float("nan")
    avg_trials = sum(r.get("trials_used", 1) for r in data) / n
    return dict(n=n, acc=acc, hc_cov=100 * len(hc) / n,
                hc_prec=hc_prec, non_hc_acc=non_hc_acc,
                gap=hc_prec - non_hc_acc,
                avg_rounds=float("nan"), avg_attempts=avg_trials)


def _reflexion_eff(data: list) -> dict:
    n = len(data)
    tokens_in = sum(r.get("tokens", {}).get("input", 0) for r in data)
    tokens_out = sum(r.get("tokens", {}).get("output", 0) for r in data)
    cost = sum(r.get("cost_usd", 0) for r in data)
    calls = 0
    for r in data:
        trials = r.get("trials", [])
        calls += 2 * r.get("trials_used", len(trials) or 1)
        calls += sum(1 for t in trials if not t.get("stable"))
    return dict(
        tokens_in=tokens_in / n,
        tokens_out=tokens_out / n,
        tokens_total=(tokens_in + tokens_out) / n,
        cost_usd=cost / n,
        calls=calls / n,
    )


def _sc_epoch_metrics(data: list) -> dict:
    """SC metrics from a parsed Epoch AI eval JSON (full_consensus = HC signal)."""
    n = len(data)
    hc     = [r for r in data if r["full_consensus"]]
    non_hc = [r for r in data if not r["full_consensus"]]
    acc        = 100 * sum(r["mv_correct"] for r in data) / n
    hc_prec    = 100 * sum(r["mv_correct"] for r in hc)     / len(hc)    if hc     else float("nan")
    non_hc_acc = 100 * sum(r["mv_correct"] for r in non_hc) / len(non_hc) if non_hc else float("nan")
    return dict(n=n, acc=acc, hc_cov=100 * len(hc) / n,
                hc_prec=hc_prec, non_hc_acc=non_hc_acc,
                gap=hc_prec - non_hc_acc,
                avg_rounds=float("nan"), avg_attempts=1.0)


def _pvd_metrics(data: list, anc_fn) -> dict:
    s = pvd_stats(data, anc_fn=anc_fn)
    avg_attempts = (sum(r.get("num_attempts", 1) for r in data) / len(data)
                    if "num_attempts" in data[0] else 1.0)
    return dict(n=s["n"], acc=s["acc"], hc_cov=s["hc_cov"],
                hc_prec=s["hc_prec"], non_hc_acc=s["non_hc_acc"], gap=s["gap"],
                avg_rounds=s["avg_rounds"], avg_attempts=avg_attempts)


# ── Efficiency: prefer logged tokens/cost, fall back to the model ────────────

def _logged_eff(data: list) -> dict | None:
    """Per-question mean input/output tokens and USD cost read directly from a
    result file's logs. Returns None when the file does not carry per-question
    token + cost logs, signalling the caller to fall back to the cost_model.py
    estimate.

    A file qualifies only if *every* record carries both a ``tokens`` block and
    a ``cost_usd`` field; partial logging is treated as absent so a row is never
    a mix of measured and estimated numbers. Handles the three token schemas in
    use: PVD-style ``{prover:{...}, verifier:{...}}``, USC-style
    ``{selector:{...}}``, and flat ``{input, output}``.
    """
    n = len(data)
    if n == 0 or not all(("tokens" in r and "cost_usd" in r) for r in data):
        return None

    def _io(rec) -> tuple[int, int]:
        t = rec["tokens"]
        if "prover" in t:                       # PVD: prover + verifier blocks
            return (t["prover"].get("input", 0) + t.get("verifier", {}).get("input", 0),
                    t["prover"].get("output", 0) + t.get("verifier", {}).get("output", 0))
        if "selector" in t:                     # USC selector pass
            return (t["selector"].get("input", 0), t["selector"].get("output", 0))
        if "input" in t or "output" in t:       # flat {input, output}
            return (t.get("input", 0), t.get("output", 0))
        # Fail loudly rather than silently logging zero tokens for an
        # unrecognized schema — a zero-token "logged" row is exactly the kind
        # of wrong result this function is meant to prevent.
        raise ValueError(f"unrecognized tokens schema: keys={sorted(t)}")

    tin = sum(_io(r)[0] for r in data)
    tout = sum(_io(r)[1] for r in data)
    cost = sum(r["cost_usd"] for r in data)
    return dict(tokens_in=tin / n, tokens_out=tout / n,
                tokens_total=(tin + tout) / n, cost_usd=cost / n)


def _eff(modeled: dict, data: list) -> dict:
    """Resolve a row's efficiency block, preferring logged tokens/cost over the
    modeled estimate. ``calls`` is always taken from ``modeled`` because it is an
    exact structural count, not a token estimate. The result is tagged with
    ``eff_source`` ('logged' or 'modeled') so summary.json is unambiguous about
    which rows are measured and which are estimated.
    """
    logged = _logged_eff(data)
    if logged is None:
        return {**modeled, "eff_source": "modeled"}
    return {**logged, "calls": modeled["calls"], "eff_source": "logged"}


# ── Run definitions ──────────────────────────────────────────────────────────
# Each entry describes how to compute a row of the summary.
# Order here is the order they'll appear in summary.json (purely cosmetic).

def _join(metrics: dict, extra: dict) -> dict:
    out = dict(metrics)
    out.update(extra)
    return out


def build_runs() -> list:
    runs = []

    # ── GPQA Diamond: Sonnet 4.6 prover ──────────────────────────────────────
    # Sonnet one-call self-deliberation ablation. Despite the historical file
    # name, this is not a plain Direct-CoT run: the single response contains
    # prover and verifier blocks and exposes an ANC signal.
    single_call_data = load(paper_data_path("gpqa_results_single_call_sonnet.json"))
    single_call = _pvd_metrics(single_call_data, anc_fn=is_anc_standard)
    runs.append(_join(
        single_call,
        dict(key="gpqa_single_call_sonnet",
             display_name="Single-call PVD (Sonnet)", method="Single-call PVD",
             prover="Sonnet 4.6", verifier="Self",
             method_family="single_call", dataset="GPQA Diamond",
             source_json="data/paper/gpqa_results_single_call_sonnet.json",
             eff=_eff(tokens_and_cost_direct("sonnet-4.6"), single_call_data))))

    usc_data = load(paper_data_path("gpqa_results_usc.json"))
    sc_m, usc_m = _sc_usc_metrics(usc_data)
    runs.append(_join(sc_m, dict(
        key="gpqa_sc_sonnet_k8",
        display_name="SC ($k{=}8$)", method="SC (k=8)",
        prover="Sonnet 4.6", verifier=None,
        method_family="baseline", dataset="GPQA Diamond",
        source_json="data/paper/gpqa_results_usc.json",
        eff=_eff(tokens_and_cost_sc("sonnet-4.6", k=8), usc_data))))
    runs.append(_join(usc_m, dict(
        key="gpqa_usc_sonnet_k8",
        display_name="USC ($k{=}8$)", method="USC (k=8)",
        prover="Sonnet 4.6", verifier="Sonnet 4.6",
        method_family="baseline", dataset="GPQA Diamond",
        source_json="data/paper/gpqa_results_usc.json",
        eff=_eff(tokens_and_cost_sc("sonnet-4.6", k=8, include_usc=True), usc_data))))

    debate_data = load(paper_data_path("gpqa_results_debate.json"))
    deb = _debate_metrics(debate_data)
    runs.append(_join(deb, dict(
        key="gpqa_debate_sonnet",
        display_name="Debate", method="Debate (3×2)",
        prover="Sonnet 4.6", verifier="Sonnet 4.6",
        method_family="baseline", dataset="GPQA Diamond",
        source_json="data/paper/gpqa_results_debate.json",
        eff=_eff(tokens_and_cost_debate("sonnet-4.6",
                                        n_agents=deb["n_agents"],
                                        n_rounds=deb["n_rounds"]), debate_data))))

    # Reflexion: source switched 2026-05-18 from the legacy
    # `gpqa_results_reflexion.json` to the clean RTTR-v1 re-run at
    # `data/gpqa_results_reflexion.json`. The legacy run had a parser
    # bug in `_extract_letter` (greedy-match of "Answer Choices") that
    # mislabeled answers and inflated the Reflexion gap; the clean
    # re-run replaces it. See README.md for the concise postmortem.
    refl_path = os.path.join(_REPO_ROOT, "data", "gpqa_results_reflexion.json")
    refl_data = load(refl_path)
    refl = _reflexion_metrics(refl_data)
    runs.append(_join(refl, dict(
        key="gpqa_reflexion_sonnet",
        display_name="Reflexion", method="Reflexion",
        prover="Sonnet 4.6", verifier=None,
        method_family="baseline", dataset="GPQA Diamond",
        source_json="data/gpqa_results_reflexion.json",
        eff=_eff(_reflexion_eff(refl_data), refl_data))))

    # PVD (Sonnet/Haiku) — diamond_full uses the old schema; ANC = final_verdict='Accept'
    pvd1_data = load(paper_data_path("gpqa_results_diamond_full.json"))
    pvd1 = _pvd_metrics(pvd1_data, anc_fn=lambda r: r.get("final_verdict") == "Accept")
    runs.append(_join(pvd1, dict(
        key="gpqa_pvd_sonnet_haiku",
        display_name="PVD (Sonnet/Haiku)", method="PVD",
        prover="Sonnet 4.6", verifier="Haiku 4.5",
        method_family="pvd_sonnet", dataset="GPQA Diamond",
        source_json="data/paper/gpqa_results_diamond_full.json",
        eff=_eff(tokens_and_cost_pvd("sonnet-4.6", "haiku-4.5",
                                     avg_rounds=pvd1["avg_rounds"]), pvd1_data))))

    pvd1c_data = load(paper_data_path("gpqa_results_challenge_sonnet_haiku.json"))
    pvd1c = _pvd_metrics(pvd1c_data, anc_fn=is_anc_standard)
    runs.append(_join(pvd1c, dict(
        key="gpqa_pvd_sonnet_haiku_challenge",
        display_name="PVD$^\\dagger$ (Sonnet/Haiku)", method=r"PVD$^\dagger$",
        prover="Sonnet 4.6", verifier="Haiku 4.5",
        method_family="pvd_sonnet", dataset="GPQA Diamond",
        source_json="data/paper/gpqa_results_challenge_sonnet_haiku.json",
        eff=_eff(tokens_and_cost_pvd("sonnet-4.6", "haiku-4.5",
                                     avg_rounds=pvd1c["avg_rounds"]), pvd1c_data))))

    # ── GPQA Diamond: GPT-5.4 prover ─────────────────────────────────────────
    gpt54_direct_data = load(paper_data_path("gpqa_results_gpt54_direct_baseline.json"))
    runs.append(_join(
        _direct_metrics(gpt54_direct_data),
        dict(key="gpqa_direct_gpt54",
             display_name="Direct (GPT-5.4)", method="Direct",
             prover="GPT-5.4", verifier=None,
             method_family="baseline", dataset="GPQA Diamond",
             source_json="data/paper/gpqa_results_gpt54_direct_baseline.json",
             eff=_eff(tokens_and_cost_direct("gpt-5.4"), gpt54_direct_data))))
    gpt54_sc_data = load(paper_data_path("gpqa_results_gpt54_sc8.json"))
    runs.append(_join(
        _sc_epoch_metrics(gpt54_sc_data),
        dict(key="gpqa_sc_gpt54_k8",
             display_name="SC$^*$ (GPT-5.4)", method=r"SC$^*$ (k=8)",
             prover="GPT-5.4", verifier=None,
             method_family="baseline", dataset="GPQA Diamond",
             source_json="data/paper/gpqa_results_gpt54_sc8.json",
             eff=_eff(tokens_and_cost_sc("gpt-5.4", k=8), gpt54_sc_data))))

    pvd_gpt54_data = load(paper_data_path("gpqa_results_gpt54_xhigh.json"))
    pvd_gpt54 = _pvd_metrics(pvd_gpt54_data, anc_fn=is_anc_standard)
    runs.append(_join(pvd_gpt54, dict(
        key="gpqa_pvd_gpt54_mini",
        display_name="PVD (GPT-5.4/mini)", method="PVD",
        prover="GPT-5.4", verifier="GPT-5.4-mini",
        method_family="pvd_cross_family", dataset="GPQA Diamond",
        source_json="data/paper/gpqa_results_gpt54_xhigh.json",
        eff=_eff(tokens_and_cost_pvd("gpt-5.4", "gpt-5.4-mini",
                                     avg_rounds=pvd_gpt54["avg_rounds"]), pvd_gpt54_data))))

    # ── GPQA Diamond: Gemini 3.1 Pro prover ──────────────────────────────────
    gemini_sc_data = load(paper_data_path("gpqa_results_gemini_pro_sc8.json"))
    runs.append(_join(
        _sc_epoch_metrics(gemini_sc_data),
        dict(key="gpqa_sc_gemini_k8",
             display_name="SC$^*$ (Gemini)", method=r"SC$^*$ (k=8)",
             prover="Gemini 3.1 Pro", verifier=None,
             method_family="baseline", dataset="GPQA Diamond",
             source_json="data/paper/gpqa_results_gemini_pro_sc8.json",
             eff=_eff(tokens_and_cost_sc("gemini-3.1-pro", k=8), gemini_sc_data))))

    pvd_gem_fl_data = load(paper_data_path("gpqa_results_gemini_pro_flashlite.json"))
    pvd_gem_fl = _pvd_metrics(pvd_gem_fl_data, anc_fn=is_anc_standard)
    runs.append(_join(pvd_gem_fl, dict(
        key="gpqa_pvd_gemini_flashlite",
        display_name="PVD (Gem./Flash-Lite)", method="PVD",
        prover="Gemini 3.1 Pro", verifier="Gemini 3.1 Flash-Lite",
        method_family="pvd_cross_family", dataset="GPQA Diamond",
        source_json="data/paper/gpqa_results_gemini_pro_flashlite.json",
        eff=_eff(tokens_and_cost_pvd("gemini-3.1-pro", "gemini-3.1-flash-lite",
                                     avg_rounds=pvd_gem_fl["avg_rounds"]), pvd_gem_fl_data))))

    pvd_gem_retry_data = load(paper_data_path("gpqa_results_gemini_3.1_pro__gpt_5.5_pro_retry.json"))
    pvd_gem_retry = _pvd_metrics(pvd_gem_retry_data, anc_fn=is_anc_retry)
    runs.append(_join(pvd_gem_retry, dict(
        key="gpqa_pvd_gemini_gpt55pro_retry",
        display_name="PVD+retry (Gem./GPT-5.5)", method="PVD+retry",
        prover="Gemini 3.1 Pro", verifier="GPT-5.5-pro",
        method_family="pvd_cross_family", dataset="GPQA Diamond",
        source_json="data/paper/gpqa_results_gemini_3.1_pro__gpt_5.5_pro_retry.json",
        eff=_eff(tokens_and_cost_pvd("gemini-3.1-pro", "gpt-5.5-pro",
                                     avg_rounds=pvd_gem_retry["avg_rounds"],
                                     avg_attempts=pvd_gem_retry["avg_attempts"]), pvd_gem_retry_data))))

    # ── HLE runs ─────────────────────────────────────────────────────────────
    for key, name, prover, verifier, prover_k, verifier_k, fname in [
        ("hle_pvd_sonnet_haiku",      "Sonnet 4.6 / Haiku 4.5",
         "Sonnet 4.6", "Haiku 4.5",
         "sonnet-4.6", "haiku-4.5",
         "hle_results_sonnet_haiku_full.json"),
        ("hle_pvd_opus_sonnet",       "Opus 4.6 / Sonnet 4.6",
         "Opus 4.6", "Sonnet 4.6",
         "opus-4.6", "sonnet-4.6",
         "hle_results_opus_challenge_full.json"),
        ("hle_pvd_gpt55_gemini",      "GPT-5.5 / Gemini 3.1 Pro",
         "GPT-5.5", "Gemini 3.1 Pro",
         "gpt-5.5-pro", "gemini-3.1-pro",
         "hle_results_gpt_5.5__gemini_3.1_pro.json"),
    ]:
        path = paper_data_path(fname)
        if not os.path.exists(path):
            print(f"  skip {fname} (not found)")
            continue
        hle_data = load(path)
        m = _pvd_metrics(hle_data, anc_fn=is_anc_standard)
        family = ("pvd_sonnet" if "Sonnet 4.6" in prover or "Opus" in prover
                  else "pvd_cross_family")
        runs.append(_join(m, dict(
            key=key, display_name=name, method="PVD",
            prover=prover, verifier=verifier,
            method_family=family, dataset="HLE",
            source_json=f"data/paper/{fname}",
            eff=_eff(tokens_and_cost_pvd(prover_k, verifier_k,
                                         avg_rounds=m["avg_rounds"],
                                         avg_attempts=m["avg_attempts"]), hle_data))))

    return runs


def _clean_nan(obj):
    """Convert NaN floats to None for JSON compliance."""
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_nan(v) for v in obj]
    if isinstance(obj, float) and math.isnan(obj):
        return None
    return obj


def _flatten(run: dict) -> dict:
    """Lift eff sub-dict into top-level keys."""
    flat = {k: v for k, v in run.items() if k != "eff"}
    eff  = run.get("eff", {})
    for k, v in eff.items():
        flat[k] = v
    return flat


def main():
    runs = build_runs()
    runs = [_flatten(r) for r in runs]
    runs = _clean_nan(runs)

    os.makedirs(TEX_DIR, exist_ok=True)
    with open(SUMMARY_PATH, "w") as f:
        json.dump(runs, f, indent=2)
        f.write("\n")
    print(f"  → {SUMMARY_PATH}  ({len(runs)} runs)")

    # Print a quick ASCII summary so the user can eyeball numbers
    print()
    print(f"{'key':38} {'n':>4} {'acc':>6} {'hc_cov':>7} {'hc_prec':>8} "
          f"{'gap':>7} {'calls':>6} {'tok':>7} {'$':>8} {'eff':>7}")
    for r in runs:
        def _f(x, w, p=1, suf=""):
            if x is None:
                return f"{'—':>{w}}"
            return f"{x:>{w}.{p}f}{suf}"
        print(
            f"{r['key']:38} "
            f"{r['n']:>4} "
            f"{_f(r['acc'], 6, 1)} "
            f"{_f(r['hc_cov'], 7, 1)} "
            f"{_f(r['hc_prec'], 8, 1)} "
            f"{_f(r['gap'], 7, 1)} "
            f"{r['calls']:>6.1f} "
            f"{r['tokens_total']:>7.0f} "
            f"{r['cost_usd']:>8.4f} "
            f"{r.get('eff_source', '?'):>7}"
        )


if __name__ == "__main__":
    main()
