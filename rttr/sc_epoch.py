"""
Convert the Epoch AI .eval archive of Sonnet 4.6 SC k=8 runs into an
rttr-v1 result JSON.

We do not re-run SC ourselves (budget) — instead we ingest Epoch AI's
samples, which include full assistant transcripts, extracted answers,
and exact token usage (input / output / reasoning).

Output:  data/gpqa_results_sc_epoch.json
Schema:  rttr-v1 (one record per question, with `samples` instead of
         `attempts` / `rounds` / `trials`)

Usage:
    python -m rttr.sc_epoch
"""

from __future__ import annotations

import json
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from rttr import SCHEMA_VERSION
from rttr.common import DATA_DIR, REPO, TOKEN_PRICES, cost_usd, TokenUsage

# Source .eval (zipped). Located at the repo root.
EVAL_PATH = REPO / "epoch_ai_sonnet_4_6_gpqa_diamond_58eQmyCfPa3FufhXJFvAL2.eval"
OUTPUT_PATH = DATA_DIR / "gpqa_results_sc_epoch.json"

CHOICES = ["A", "B", "C", "D"]


def _extract_letter(text: str) -> str | None:
    """Pull the answer letter from a 'ANSWER: X' line (Epoch eval format)."""
    m = re.search(r"ANSWER:\s*([A-Da-d])", text)
    if m:
        return m.group(1).upper()
    # Fallback: last bare A-D in the final 300 chars
    matches = re.findall(r"\b([A-Da-d])\b", text[-300:])
    return matches[-1].upper() if matches else None


def _extract_text(content) -> str:
    """Concatenate the text+reasoning pieces of an assistant message."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for block in content:
            t = block.get("type")
            if t == "text":
                out.append(block.get("text", ""))
            elif t == "reasoning":
                summary = block.get("summary", "")
                if summary:
                    out.append(f"[reasoning] {summary}")
        return "\n\n".join(out)
    return str(content)


def _extract_question(content) -> str:
    """Return the raw user prompt (question + choices)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for b in content:
            if b.get("type") == "text":
                return b.get("text", "")
    return ""


def parse_eval(path: Path) -> list[dict]:
    """Group samples by question_id, return one record per question."""
    by_q: dict[str, list[dict]] = defaultdict(list)
    with zipfile.ZipFile(path) as zf:
        sample_names = [n for n in zf.namelist() if n.startswith("samples/")]
        for name in sample_names:
            with zf.open(name) as f:
                s = json.load(f)
            messages = s.get("messages", [])
            if len(messages) < 2:
                continue
            user_msg, assistant_msg = messages[0], messages[1]
            question = _extract_question(user_msg.get("content", ""))
            response = _extract_text(assistant_msg.get("content", ""))
            answer = (s.get("scores", {}).get("choice", {}).get("answer")
                      or _extract_letter(response))
            usage = s.get("output", {}).get("usage", {}) or {}
            by_q[s["id"]].append({
                "epoch": s.get("epoch", 1),
                "question": question,
                "response_text": response,
                "answer": answer,
                "target": s.get("target"),
                "tokens_in":     usage.get("input_tokens", 0),
                "tokens_out":    usage.get("output_tokens", 0),
                "thinking_tokens": usage.get("reasoning_tokens", 0),
                "wall_seconds":  s.get("total_time", None),
            })

    records: list[dict] = []
    for qid, samples in by_q.items():
        samples.sort(key=lambda x: x["epoch"])
        valid = [s for s in samples if s["answer"] and s["target"]]
        if not valid:
            continue
        target = valid[0]["target"]
        question = valid[0]["question"]
        answers = [s["answer"] for s in valid]
        k = len(answers)
        counts = Counter(answers)
        mv_answer, mv_count = counts.most_common(1)[0]
        full_consensus = (len(counts) == 1)
        agreement_rate = mv_count / k
        correct = (mv_answer == target)

        # Aggregate tokens
        tot_in  = sum(s["tokens_in"] for s in samples)
        tot_out = sum(s["tokens_out"] for s in samples)
        tot_thk = sum(s["thinking_tokens"] for s in samples)
        usage = TokenUsage(input=tot_in, output=tot_out, thinking=tot_thk)
        cost = cost_usd(usage, "claude-sonnet-4-6")
        wall = sum((s["wall_seconds"] or 0) for s in samples)

        records.append({
            "question_num":     qid,
            "correct_letter":   target,
            "sc_answer":        mv_answer,
            "correct":          correct,
            "agreement_rate":   agreement_rate,
            "full_consensus":   full_consensus,
            "outcome":          "full_consensus" if full_consensus else "split",
            "k":                k,
            "question":         question,
            "samples": [
                {
                    "epoch":              s["epoch"],
                    "answer":             s["answer"],
                    "text":               s["response_text"],
                    "tokens_in":          s["tokens_in"],
                    "tokens_out":         s["tokens_out"],
                    "thinking_tokens":    s["thinking_tokens"],
                }
                for s in samples
            ],
            "tokens": {
                "prover": usage.to_dict(),
            },
            "cost_usd":         round(cost, 6),
            "wall_seconds":     round(wall, 2),
            "model":            "claude-sonnet-4-6",
            "schema_version":   SCHEMA_VERSION,
        })
    return records


def main():
    if not EVAL_PATH.exists():
        raise FileNotFoundError(f"Epoch eval not found: {EVAL_PATH}")
    print(f"Parsing {EVAL_PATH.name}...")
    records = parse_eval(EVAL_PATH)
    print(f"  {len(records)} questions, "
          f"{sum(len(r['samples']) for r in records)} total samples")

    # Build _meta record
    meta = {
        "_meta": True,
        "run_key": "sc_epoch",
        "source": "Epoch AI .eval archive (Sonnet 4.6, GPQA Diamond, k=8, "
                  "extended thinking; we did NOT re-run SC ourselves)",
        "model": "claude-sonnet-4-6",
        "n_samples_per_question": 8,
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": SCHEMA_VERSION,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump([meta] + records, f, indent=2)
    print(f"  → {OUTPUT_PATH}")

    # Quick summary
    n = len(records)
    correct = sum(r["correct"] for r in records)
    hc = [r for r in records if r["full_consensus"]]
    non_hc = [r for r in records if not r["full_consensus"]]
    hc_prec = 100 * sum(r["correct"] for r in hc) / len(hc) if hc else float("nan")
    non_hc_acc = 100 * sum(r["correct"] for r in non_hc) / len(non_hc) if non_hc else float("nan")
    total_cost = sum(r["cost_usd"] for r in records)
    total_in = sum(r["tokens"]["prover"]["input"] for r in records)
    total_out = sum(r["tokens"]["prover"]["output"] for r in records)
    print()
    print(f"  SC summary (Epoch AI data):")
    print(f"    Overall acc:    {100 * correct / n:.1f}%   (n={n})")
    print(f"    HC cov:         {100 * len(hc) / n:.1f}%   (full consensus)")
    print(f"    HC prec:        {hc_prec:.1f}%")
    print(f"    Non-HC acc:     {non_hc_acc:.1f}%")
    print(f"    Gap:            {hc_prec - non_hc_acc:+.1f}pp")
    print(f"    Tokens (input): {total_in:,}")
    print(f"    Tokens (out):   {total_out:,}")
    print(f"    Total cost:     ${total_cost:.2f}   (${total_cost / n:.4f}/q)")


if __name__ == "__main__":
    main()
