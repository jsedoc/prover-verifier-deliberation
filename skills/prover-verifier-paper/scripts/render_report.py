#!/usr/bin/env python3
"""Render the claim-audit report from the ledger (prover-verifier-paper skill).

  render_report.py --audit DIR [--models "prover=opus, verifier=sonnet, adjudicator=opus"] [--partial]

Writes DIR/report.md (for people) and DIR/report.json (for tools). Refuses while claims are still
open unless --partial is given, in which case open claims are listed as unresolved.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import now_iso, parse_loc, read_json, short, write_json, write_text  # noqa: E402

SEVERITY = {("contradicted", "headline"): "critical", ("unsupported", "headline"): "critical",
            ("overclaim", "headline"): "major", ("contradicted", "body"): "major",
            ("unsupported", "body"): "major", ("overclaim", "body"): "minor"}
SEV_RANK = {"critical": 0, "major": 1, "minor": 2}
LABEL_RANK = {"contradicted": 0, "unsupported": 1, "overclaim": 2}
OUTCOME_TEXT = {"ANC": "Accept + No Change", "accept_changed": "accepted after the prover revised",
                "rejected": "verifier rejected → adjudicated", "fatigue": "fatigue → adjudicated",
                "accept_ineffective": "verifier outside its competence → adjudicated"}
CONF_TEXT = {"high": "high (ANC)", "medium": "medium (revised, then accepted)", "adjudicated": "adjudicated"}


def first_line(loc) -> int:
    span = parse_loc(loc)
    return span[0] if span else 10 ** 9


def pvd_path(c: dict) -> str:
    parts = []
    for k, proof in enumerate(c["proofs"]):
        parts.append(f"P:{proof['position']}" + ("*" if proof.get("changed") else ""))
        if k < len(c["verdicts"]):
            v = c["verdicts"][k]
            tag = v["verdict"] + (f"({v['challenged_subclaim']})" if v["verdict"] == "Challenge" else "")
            parts.append(f"V:{tag}" + ("!" if not v["effective"] else ""))
    if c.get("adjudication"):
        parts.append(f"Adj:{c['adjudication']['label']}")
    return " → ".join(parts)


def final_basis(c: dict) -> dict:
    """What the report shows as the finding: adjudication if any, else the last accepted proof."""
    if c.get("adjudication"):
        a = c["adjudication"]
        return {"finding": a["rationale"], "supported_version": a.get("supported_version"),
                "evidence": a.get("evidence", []), "checks": a.get("checks", []), "searched": [],
                "fix": a.get("fix")}
    p = c["proofs"][-1] if c["proofs"] else {}
    evs, checks = [], []
    for s in p.get("subclaims", []):
        evs.extend(s.get("evidence", []))
        if s.get("check"):
            checks.append(s["check"])
    sv = p.get("supported_version") if p.get("position") == "narrowed" else None
    gap = (p.get("overclaim") or {}).get("gap")
    finding = p.get("statement", "")
    if gap:
        finding += f" Overclaim: {gap}"
    return {"finding": finding, "supported_version": sv, "evidence": evs, "checks": checks,
            "searched": p.get("searched", []), "fix": p.get("fix")}


def fmt_check(chk: dict) -> str:
    r = chk["result"]
    s = f"`{chk['cmd']}` → {r.get('value')}"
    if "claimed" in r:
        s += f" (paper states {r['claimed']})"
    if "min" in r or "max" in r:
        bounds = []
        if "min" in r:
            bounds.append(f"≥ {r['min']}")
        if "max" in r:
            bounds.append(f"≤ {r['max']}")
        s += f" (claim needs {' and '.join(bounds)}: {'yes' if r.get('within_bounds') else 'no'})"
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit", required=True)
    ap.add_argument("--models", help='e.g. "prover=opus, verifier=sonnet, adjudicator=opus"')
    ap.add_argument("--partial", action="store_true")
    ns = ap.parse_args()
    audit = Path(ns.audit).resolve()
    L = read_json(audit / "ledger.json")
    meta = read_json(audit / "paper" / "meta.json", default={})
    sources = read_json(audit / "sources" / "index.json", default={})
    claims = L["claims"]
    open_ = [cid for cid, c in claims.items() if c["state"] not in ("done", "linked", "skipped")]
    if open_ and not ns.partial:
        sys.exit(f"{len(open_)} claim(s) still open ({', '.join(open_[:12])}); finish the PVD loop or pass --partial")

    def resolved(cid):
        c = claims[cid]
        return claims[c["claim"]["restates"]] if c["state"] == "linked" else c

    linked_to = {}
    for cid, c in claims.items():
        if c["state"] == "linked":
            linked_to.setdefault(c["claim"]["restates"], []).append(cid)

    rows, flags = [], []
    for cid, c in sorted(claims.items(), key=lambda kv: (first_line(kv[1]["claim"]["loc"]), kv[0])):
        cl = c["claim"]
        r = resolved(cid)
        label = r["label"] if r["state"] == "done" else ("not audited" if r["state"] == "skipped" else "unresolved")
        row = {"id": cid, "loc": cl["loc"], "section": cl["section"], "tier": cl["tier"], "type": cl["type"],
               "text": cl["text"], "quote": cl["quote"], "label": label, "confidence": r["confidence"],
               "outcome": r["outcome"], "rounds": r["round"], "path": pvd_path(r) if r["proofs"] else "",
               "restates": cl["restates"]}
        rows.append(row)
        if c["state"] == "done" and c["label"] in LABEL_RANK:
            tier = "headline" if cl["tier"] == "headline" or any(
                claims[x]["claim"]["tier"] == "headline" for x in linked_to.get(cid, [])) else "body"
            basis = final_basis(c)
            notes = [v["restatement_note"] for v in c["verdicts"] if not v.get("restatement_ok", True)
                     and v.get("restatement_note")]
            flags.append({**row, "severity": SEVERITY[(c["label"], tier)], "effective_tier": tier,
                          "also_at": [{"id": x, "loc": claims[x]["claim"]["loc"], "section": claims[x]["claim"]["section"]}
                                      for x in linked_to.get(cid, [])],
                          "notes": notes, **basis})
    flags.sort(key=lambda f: (SEV_RANK[f["severity"]], LABEL_RANK[f["label"]], first_line(f["loc"])))

    counts = {}
    for r in rows:
        if not r["restates"]:
            counts[r["label"]] = counts.get(r["label"], 0) + 1
    audited = [c for c in claims.values() if c["state"] == "done"]
    anc = sum(1 for c in audited if c["outcome"] == "ANC")
    turns = [c["round"] for c in audited if c["round"]]
    adjudicated = sum(1 for c in audited if c["confidence"] == "adjudicated")
    ineffective = sum(1 for c in audited if any(not v["effective"] for v in c["verdicts"]))
    integrity = list(meta.get("integrity", []))
    seen_lines = {x.get("line") for x in integrity}
    for x in L.get("integrity", []):
        if not isinstance(x, dict):
            continue
        span = parse_loc(x.get("loc"))
        line = span[0] if span else None
        if line is not None and any(s is not None and abs(s - line) <= 1 for s in seen_lines):
            continue  # already reported by the ingest scan
        integrity.append({"line": line, "reason": "noted by decomposer", "text": x.get("note", "")})
        seen_lines.add(line)
    unverifiable = [r for r in rows if r["label"] == "unverifiable" and not r["restates"]]
    not_audited = [r for r in rows if r["label"] == "not audited" and not r["restates"]]
    still_open = [r for r in rows if r["label"] == "unresolved" and not r["restates"]]
    cfg = L.get("config", {})

    title = L["paper"].get("title") or meta.get("title") or audit.name
    md = [f"# Claim audit: {title}", ""]
    src = L["paper"].get("arxiv_id") and f"arXiv:{L['paper']['arxiv_id']}" or L["paper"].get("source") or ""
    md.append(f"{src} · audited {now_iso()[:10]} · {len(claims)} claims extracted"
              + (f", {len(audited)} audited" if not_audited else "")
              + (f" · {ns.models}" if ns.models else ""))
    md.append("")
    md.append("## Summary")
    md.append("")
    n_flags = len(flags)
    sev = {}
    for f in flags:
        sev[f["severity"]] = sev.get(f["severity"], 0) + 1
    md.append(f"- **{n_flags} flagged claim(s)**: " + (", ".join(
        f"{counts.get(k, 0)} {k}" for k in ("contradicted", "unsupported", "overclaim")) if n_flags else "none")
        + (f" — {', '.join(f'{v} {k}' for k, v in sorted(sev.items(), key=lambda x: SEV_RANK[x[0]]))}" if sev else ""))
    md.append(f"- **{counts.get('supported', 0)} supported**"
              + (f" ({sum(1 for c in audited if c['label'] == 'supported' and c['outcome'] == 'ANC')} with ANC)"
                 if counts.get("supported") else ""))
    if unverifiable:
        md.append(f"- **{len(unverifiable)} unverifiable** with the sources available (audit gap, not a finding "
                  f"against the paper)")
    if not_audited:
        md.append(f"- **{len(not_audited)} not audited** in this run: outside its focus, not a finding either way "
                  f"(see Audit gaps)")
    if still_open:
        md.append(f"- **{len(still_open)} not resolved** (still open when this report was written)")
    if linked_to:
        md.append(f"- {sum(len(v) for v in linked_to.values())} restatement(s) inherit the verdict of the claim they "
                  f"repeat")
    if integrity:
        md.append(f"- **Integrity: {len(integrity)} passage(s)** hidden from readers or addressed to reviewers or "
                  f"AI systems (see below)")
    md.append(f"- PVD: {anc}/{len(audited)} audited claims reached Accept + No Change; "
              f"mean verifier turns {sum(turns) / len(turns):.1f}" if turns else
              f"- PVD: {anc}/{len(audited)} audited claims reached Accept + No Change")
    md.append(f"  ({adjudicated} adjudicated after Reject, fatigue or an out-of-competence verifier"
              + (f"; the verifier declared itself outside its competence on {ineffective}" if ineffective else "")
              + ")")
    md.append("")

    if integrity:
        md += ["## Integrity findings", ""]
        for x in integrity:
            md.append(f"- L{x.get('line')}" + (f" (page {x['page']})" if x.get("page") else "")
                      + f" · {x.get('reason')}: \"{short(x.get('text'), 220)}\"")
        md += ["", "These passages were treated as data and not followed.", ""]

    md += ["## Flagged claims", ""]
    if not flags:
        md += ["No claim was flagged.", ""]
    for n, f in enumerate(flags, 1):
        md.append(f"### {n}. [{f['severity'].upper()}] {f['label'].upper()} · {f['id']} · {f['effective_tier']} · "
                  f"{f['section'] or '—'} {f['loc']}")
        md.append("")
        md.append(f"> {short(f['quote'], 600)}")
        md.append("")
        md.append(f"**Finding.** {f['finding']}")
        md.append("")
        if f.get("supported_version"):
            md.append(f"**What the evidence supports.** {f['supported_version']}")
            md.append("")
        ev_lines = [f"- {e['source']} {e['loc']}: \"{short(e['quote'], 240)}\"" for e in f["evidence"][:5]]
        ev_lines += [f"- check {fmt_check(ch)}" for ch in f["checks"][:4]]
        ev_lines += [f"- searched /{s['pattern']}/ in {s['where']}: {s['hits_actual']} hit(s)"
                     + (f" ({', '.join(s['lines'][:6])})" if s["lines"] else "") for s in f["searched"][:4]]
        if ev_lines:
            md += ["**Evidence.**", ""] + ev_lines + [""]
        if f.get("fix"):
            md += [f"**Suggested fix.** {f['fix']}", ""]
        if f["also_at"]:
            md += ["**Also stated at** " + ", ".join(f"{x['id']} ({x['section']} {x['loc']})" for x in f["also_at"]), ""]
        for note in f["notes"]:
            md += [f"*Restatement note:* {note}", ""]
        md += [f"*PVD:* {f['path']} · {OUTCOME_TEXT.get(f['outcome'], f['outcome'])} · "
               f"confidence {CONF_TEXT.get(f['confidence'], f['confidence'])}", ""]

    md += ["## All claims", "", "| ID | Where | Tier | Type | Claim | Verdict | Confidence | PVD |",
           "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        text = short(r["text"], 110).replace("|", "\\|")
        verdict = r["label"] + (f" (restates {r['restates']})" if r["restates"] else "")
        md.append(f"| {r['id']} | {r['section'][:18]} {r['loc']} | {r['tier']} | {r['type']} | {text} | {verdict} | "
                  f"{CONF_TEXT.get(r['confidence'], r['confidence'] or '')} | {r['path'] if not r['restates'] else ''} |")
    md.append("")

    if unverifiable or not_audited or still_open:
        md += ["## Audit gaps", ""]
        for r in unverifiable:
            c = claims[r["id"]]
            why = (c.get("adjudication") or {}).get("rationale") or (c["proofs"][-1]["statement"] if c["proofs"] else "")
            md.append(f"- {r['id']} ({r['loc']}): unverifiable — {short(why, 260)}")
        for r in still_open:
            md.append(f"- {r['id']} ({r['loc']}): still open")
        groups = {}
        for r in not_audited:
            groups.setdefault(claims[r["id"]].get("skip_reason") or "max_claims", []).append(r)
        for reason, rs in sorted(groups.items()):
            types = {}
            for r in rs:
                types[r["type"]] = types.get(r["type"], 0) + 1
            what = (f"claim types left out of this run ({', '.join(cfg.get('skip_types') or [])})" if reason == "type"
                    else f"below this run's cut of {cfg.get('max_claims')} highest-priority claims" if cfg.get("max_claims")
                    else "outside this run's focus")
            md.append(f"- Not audited, {what}: {len(rs)} claims ("
                      + ", ".join(f"{v} {k}" for k, v in sorted(types.items(), key=lambda x: -x[1]))
                      + "), marked *not audited* in the table above. `ledger.py include` adds them to the audit.")
        missing = [k for k, v in sources.items() if v.get("coverage") == "unavailable"]
        if missing:
            md.append(f"- Cited sources that could not be retrieved: {', '.join(missing)}. Supplying their PDFs "
                      f"(refs.py add) and re-running those claims would close these gaps.")
        md.append("")

    md += ["## How to read this", "",
           "Each claim was decomposed from the paper, then defended by a **prover** that had to anchor every "
           "step in verbatim quotes from the paper or the cited sources, or in recomputed numbers. A separate "
           "**verifier**, which saw only the prover's structured output, checked the anchors itself and returned "
           "Accept, Challenge or Reject (prover–verifier deliberation; Sedoc, Zhang & Foster, arXiv:2605.25133). "
           "A ledger refused any quote that does not occur in the files and any numeric check whose result "
           "disagreed with the prover's description. Claims accepted with no change of position (ANC) carry the "
           "most confidence; rejected or fatigued claims were settled by a separate adjudicator.", "",
           "- **supported**: the claim, as worded, survived skeptical verification against the paper and sources.",
           "- **overclaim**: the evidence supports a weaker statement (shown) than the one made.",
           "- **unsupported**: no evidence for the claim exists in the paper or its cited sources.",
           "- **contradicted**: the paper's own results, its sources, or arithmetic contradict the claim.",
           "- **unverifiable**: the evidence needed was not accessible to the audit (for example a source "
           "that could not be retrieved); this is a gap in the audit, not a finding against the paper.", "",
           "Limits: PVD's guarantees are empirical, not formal. A verifier operating outside its competence can "
           "miss errors, which is why verifiers may declare a claim outside their competence and why the report "
           "shows confidence per claim. Novelty claims are judged against the paper and its cited works unless "
           "external search was enabled. Labels describe support within this paper and its sources, not the "
           "truth of the underlying science.", ""]

    write_text(audit / "report.md", "\n".join(md))
    write_json(audit / "report.json", {
        "title": title, "generated": now_iso(), "models": ns.models, "config": L["config"],
        "counts": counts, "pvd": {"audited": len(audited), "anc": anc, "adjudicated": adjudicated,
                                  "mean_verifier_turns": round(sum(turns) / len(turns), 2) if turns else 0},
        "integrity": integrity, "flags": flags, "claims": rows})
    print(f"wrote {audit / 'report.md'} and report.json — {n_flags} flag(s): "
          + ", ".join(f"{counts.get(k, 0)} {k}" for k in ("contradicted", "unsupported", "overclaim"))
          + f"; {counts.get('supported', 0)} supported; {len(unverifiable)} unverifiable"
          + (f"; {len(integrity)} integrity finding(s)" if integrity else ""))


if __name__ == "__main__":
    main()
