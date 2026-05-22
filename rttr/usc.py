"""
Universal Self-Consistency (USC) — Chen et al. 2024.

We reuse the Epoch AI SC k=8 samples (parsed by rttr/sc_epoch.py) rather
than re-running SC. The only new API call per question is the *selector*
pass: Sonnet sees the question plus the k candidate responses and picks
the integer index of the most internally-consistent one. The USC answer
is the letter chosen by that candidate.

HC signal: full consensus across the k=8 samples (same as SC). USC and
SC have identical coverage; the only metric USC can change is HC-Prec on
the non-full-consensus subset.

Cost: ~$0.04–0.10 per question (one Sonnet call seeing 8 long responses).

Usage:
    python -m rttr.usc                          # reads default SC source
    python -m rttr.usc --config configs/usc.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from rttr import SCHEMA_VERSION
from rttr.common import (
    TOKEN_PRICES, TokenUsage, cost_usd, finalize_meta, load_config, load_prompt,
    load_existing, make_meta, save_results, DATA_DIR, REPO,
)


RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
GREEN = "\033[92m"; RED = "\033[91m"; YELLOW = "\033[93m"; CYAN = "\033[96m"

CHOICES = ["A", "B", "C", "D"]
SC_SOURCE_DEFAULT = DATA_DIR / "gpqa_results_sc_epoch.json"


_client = None
def get_client():
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


async def _api_call(model: str, system: str, messages: list,
                    temperature: float = 0.0, max_tokens: int = 80,
                    qid: str = "", retries: int = 3) -> tuple[str, TokenUsage]:
    client = get_client()
    for attempt in range(retries):
        try:
            resp = await client.messages.create(
                model=model, max_tokens=max_tokens,
                system=system, messages=messages,
                temperature=temperature,
            )
            text = ""
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    text += block.text
                elif hasattr(block, "text"):
                    text += block.text
            return text, TokenUsage.from_anthropic(resp.usage)
        except Exception as e:
            print(f"{RED}  [{qid}] API error (try {attempt+1}): {e}{RESET}", flush=True)
            await asyncio.sleep(2 ** attempt)
    return "", TokenUsage()


def _extract_index(text: str, k: int) -> int | None:
    """Pull a candidate index 1..k from the selector's response."""
    m = re.search(r"\b([1-9]\d?)\b", text)
    if m:
        n = int(m.group(1))
        if 1 <= n <= k:
            return n
    return None


def _format_candidates(samples: list[dict]) -> str:
    """Build the 'Candidate i:\\n<text>' block for the selector prompt."""
    pieces = []
    for i, s in enumerate(samples, start=1):
        # Truncate very long candidate texts so the selector prompt stays
        # within reasonable bounds. 4000 chars ≈ ~1000 tokens.
        text = s["text"]
        if len(text) > 4000:
            text = text[:4000] + " ...[truncated]"
        pieces.append(f"Candidate {i}:\n{text}\n")
    return "\n".join(pieces)


async def run_question(sc_record: dict, model: str) -> dict:
    qid = sc_record["question_num"]
    samples = sc_record["samples"]
    k = len(samples)
    question = sc_record["question"]
    target = sc_record["correct_letter"]

    selector_sys = load_prompt("usc_selector_system")
    selector_user = load_prompt(
        "usc_selector_user",
        k=k, question=question,
        candidates=_format_candidates(samples),
    )

    t0 = time.time()
    text, usage = await _api_call(model, selector_sys,
                                  [{"role": "user", "content": selector_user}],
                                  temperature=0.0, max_tokens=80, qid=qid)
    wall = time.time() - t0

    idx = _extract_index(text, k)
    if idx is None:
        # Fallback: majority vote
        answers = [s["answer"] for s in samples]
        mv = Counter(answers).most_common(1)[0][0]
        usc_answer = mv
        selector_choice = None
    else:
        usc_answer = samples[idx - 1]["answer"]
        selector_choice = idx

    correct = (usc_answer == target)
    cost = cost_usd(usage, model)

    icon = GREEN + "✓" + RESET if correct else RED + "✗" + RESET
    print(f"  {icon} {qid}: usc={BOLD}{usc_answer}{RESET} sc={sc_record['sc_answer']} "
          f"target={target} idx={selector_choice} "
          f"full_consensus={sc_record['full_consensus']} cost=${cost:.4f}",
          flush=True)

    return {
        "question_num":     qid,
        "correct_letter":   target,
        "sc_answer":        sc_record["sc_answer"],
        "usc_answer":       usc_answer,
        "correct":          correct,
        "selector_choice":  selector_choice,
        "selector_text":    text,
        "agreement_rate":   sc_record["agreement_rate"],
        "full_consensus":   sc_record["full_consensus"],
        "outcome":          ("full_consensus" if sc_record["full_consensus"]
                              else "split"),
        "k":                k,
        "question":         sc_record["question"],
        "samples": [
            {"epoch": s["epoch"], "answer": s["answer"]}
            for s in samples
        ],
        # USC tokens are just the selector call; SC tokens are reported
        # separately by sc_epoch.py.
        "tokens": {
            "selector": usage.to_dict(),
        },
        "selector_cost_usd": round(cost, 6),
        "wall_seconds":     round(wall, 2),
        "model":            model,
        "schema_version":   SCHEMA_VERSION,
    }


async def run_async(config: dict):
    usc_cfg = config.get("usc", {})
    model = usc_cfg.get("model", "claude-sonnet-4-6")
    sc_source = usc_cfg.get("sc_source")
    if sc_source:
        sc_path = Path(sc_source)
        if not sc_path.is_absolute():
            sc_path = REPO / sc_path
    else:
        sc_path = SC_SOURCE_DEFAULT

    out_path = config["logging"]["_output_abs"]
    concurrent = int(config["compute"].get("concurrent", 5))

    # Load SC samples
    with open(sc_path) as f:
        sc_data = json.load(f)
    sc_records = [r for r in sc_data if isinstance(r, dict) and not r.get("_meta")]

    # Resume support
    prev_meta, prev_records = load_existing(out_path)
    done = {r["question_num"] for r in prev_records
            if r.get("schema_version") == SCHEMA_VERSION}

    started_at = datetime.now(timezone.utc)
    meta = prev_meta or make_meta(
        config, started_at, schema_version=SCHEMA_VERSION,
        extra={"model": model, "sc_source": str(sc_path), "n_candidates": 8},
    )

    todo = [r for r in sc_records if r["question_num"] not in done]
    print(f"{CYAN}{BOLD}USC run · {config['run_key']} · selector={model} "
          f"sc_source={sc_path.name}{RESET}")
    print(f"{DIM}  Will process {len(todo)} / {len(sc_records)}, "
          f"concurrent={concurrent}{RESET}\n")

    sem = asyncio.Semaphore(concurrent)
    results = list(prev_records)
    lock = asyncio.Lock()

    async def _run_one(rec):
        async with sem:
            out = await run_question(rec, model)
            async with lock:
                results.append(out)
                results.sort(key=lambda r: r["question_num"])
                save_results(out_path, meta, results)

    await asyncio.gather(*[_run_one(r) for r in todo])

    meta = finalize_meta(meta, datetime.now(timezone.utc))
    save_results(out_path, meta, sorted(results, key=lambda r: r["question_num"]))
    _print_summary(results, model)
    print(f"\n  Saved to {out_path}\n")


def _print_summary(records, model):
    n = len(records)
    if not n: return
    correct = sum(r["correct"] for r in records)
    hc = [r for r in records if r["full_consensus"]]
    non_hc = [r for r in records if not r["full_consensus"]]
    hc_prec = 100 * sum(r["correct"] for r in hc) / len(hc) if hc else float("nan")
    non_hc_acc = 100 * sum(r["correct"] for r in non_hc) / len(non_hc) if non_hc else float("nan")
    total_sel_cost = sum(r["selector_cost_usd"] for r in records)
    print(f"\n{BOLD}{CYAN}  SUMMARY · n={n}{RESET}")
    print(f"  Overall acc:        {correct}/{n} = {100*correct/n:.1f}%")
    print(f"  HC coverage:        {100*len(hc)/n:.1f}% (full consensus)")
    print(f"  HC precision:       {hc_prec:.1f}%")
    print(f"  Non-HC acc:         {non_hc_acc:.1f}%")
    print(f"  Gap:                {hc_prec - non_hc_acc:+.1f}pp")
    print(f"  Selector cost only: ${total_sel_cost:.2f}")


def main(config: dict):
    asyncio.run(run_async(config))


if __name__ == "__main__":
    # Allow direct `python -m rttr.usc --config configs/usc.yaml`
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="configs/usc.yaml")
    args = p.parse_args()
    cfg = load_config(args.config)
    main(cfg)
