"""
Single-call self-deliberation eval.

One Sonnet 4.6 inference per question. The model plays both roles in
sequence within the same response, alternating [PROVER] / [VERIFIER]
blocks until either the verifier issues Accept/Reject or it hits the
fatigue limit. ANC signal: final_verdict == "Accept" AND no answer
change across [PROVER] blocks.

Cost: ~1 long Sonnet call per question (~$0.015–$0.03 each).

Output schema: rttr-v1 (verbose transcript, per-question token rollup,
cost_usd, wall_seconds).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone

import anthropic

from rttr import SCHEMA_VERSION
from rttr.common import (
    CHOICES, MCQItem, TokenUsage,
    cost_usd, extract_json, finalize_meta, format_mcq,
    load_existing, load_gpqa_diamond, load_prompt, make_meta, save_results,
)


RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
GREEN = "\033[92m"; RED = "\033[91m"; YELLOW = "\033[93m"; CYAN = "\033[96m"


_client = None
def get_client():
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


async def _api_call(model: str, system: str, messages: list,
                    max_tokens: int = 6000,
                    qid: str = "", retries: int = 3) -> tuple[str, TokenUsage]:
    client = get_client()
    for attempt in range(retries):
        try:
            resp = await client.messages.create(
                model=model, max_tokens=max_tokens,
                system=system, messages=messages,
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


def _parse_response(raw: str) -> dict:
    """Pull PROVER/VERIFIER blocks and FINAL_ANSWER/FINAL_VERDICT out of the raw text."""
    prover_blocks = []
    for m in re.finditer(r"\[PROVER\](.*?)\[/PROVER\]", raw, re.DOTALL):
        data = extract_json(m.group(1).strip())
        if data:
            prover_blocks.append(data)
    verifier_blocks = []
    for m in re.finditer(r"\[VERIFIER\](.*?)\[/VERIFIER\]", raw, re.DOTALL):
        data = extract_json(m.group(1).strip())
        if data:
            verifier_blocks.append(data)
    final_answer = "?"
    final_verdict = "Reject"
    fa = re.search(r"FINAL_ANSWER:\s*([A-Da-d])", raw)
    if fa:
        final_answer = fa.group(1).upper()
    fv = re.search(r"FINAL_VERDICT:\s*(\S+(?:\([^)]+\))?)", raw)
    if fv:
        final_verdict = fv.group(1)
    if final_answer == "?" and prover_blocks:
        last = str(prover_blocks[-1].get("answer", "")).strip().upper()[:1]
        if last in CHOICES:
            final_answer = last
    return dict(final_answer=final_answer, final_verdict=final_verdict,
                rounds_used=len(verifier_blocks),
                prover_blocks=prover_blocks,
                verifier_blocks=verifier_blocks)


def _answer_changes(prover_blocks: list[dict]) -> int:
    answers = [str(b.get("answer", "")).strip().upper()[:1] for b in prover_blocks]
    answers = [a for a in answers if a in CHOICES]
    if len(answers) < 2:
        return 0
    return sum(1 for a, b in zip(answers, answers[1:]) if a != b)


async def run_question(item: MCQItem, qnum: int, total: int,
                       model: str, fatigue_limit: int) -> dict:
    qid = f"Q{qnum}"
    start = time.time()
    system = load_prompt("single_call_system", fatigue=fatigue_limit)
    mcq_text = format_mcq(item)

    raw, usage = await _api_call(model, system,
                                 [{"role": "user", "content": mcq_text}],
                                 max_tokens=6000, qid=qid)
    parsed = _parse_response(raw)
    final_answer = parsed["final_answer"]
    final_verdict = parsed["final_verdict"]
    rounds_used = parsed["rounds_used"]
    answer_changes = _answer_changes(parsed["prover_blocks"])
    initial_answer = (str(parsed["prover_blocks"][0].get("answer", "")).strip().upper()[:1]
                       if parsed["prover_blocks"] else "?")

    correct = (final_answer == item.correct_letter)

    # Belt-and-suspenders min-1-challenge enforcement (rule #2 in the system
    # prompt requires the verifier to issue a Challenge on round 1). If the
    # model accepted on round 1 anyway, the verifier did not exercise its
    # adversarial role and the resulting Accept is invalid. Mark such cases
    # as "accept_no_challenge" so they are excluded from the ANC subset.
    vbs = parsed["verifier_blocks"]
    rule2_violated = bool(vbs) and vbs[0].get("verdict") == "Accept"

    # ANC: final Accept + no answer changes across prover blocks
    if rule2_violated:
        outcome = "accept_no_challenge"   # invalid — round-1 Accept
    elif final_verdict == "Accept" and answer_changes == 0:
        outcome = "accept_no_change"
    elif final_verdict == "Accept":
        outcome = "accept_changed"
    elif "fatigue" in final_verdict.lower():
        outcome = "fatigue"
    else:
        outcome = "reject"

    cost = cost_usd(usage, model)
    wall = time.time() - start

    icon = GREEN + "✓" + RESET if correct else RED + "✗" + RESET
    print(f"  {icon} {qid}: ans={BOLD}{final_answer}{RESET} "
          f"correct={item.correct_letter} verdict={final_verdict} "
          f"changes={answer_changes} rounds={rounds_used} "
          f"cost=${cost:.4f}", flush=True)

    return {
        "question_num":     qnum,
        "subdomain":        item.subdomain,
        "domain":           item.domain,
        "question":         item.question,
        "choices":          item.choices,
        "correct_letter":   item.correct_letter,
        "prover_answer":    final_answer,
        "initial_answer":   initial_answer,
        "correct":          correct,
        "final_verdict":    final_verdict,
        "outcome":          outcome,
        "rounds_used":      rounds_used,
        "answer_changes":   answer_changes,
        "rule2_violated":   rule2_violated,
        "fatigue_limit":    fatigue_limit,
        # Single-attempt protocol: keep these for cross-protocol compatibility
        "num_attempts":     1,
        "total_rounds":     rounds_used,
        "transcript": {
            "raw":             raw,
            "prover_blocks":   parsed["prover_blocks"],
            "verifier_blocks": parsed["verifier_blocks"],
        },
        "tokens": {
            "prover": usage.to_dict(),
        },
        "cost_usd":         round(cost, 6),
        "wall_seconds":     round(wall, 2),
        "model":            model,
        "schema_version":   SCHEMA_VERSION,
    }


async def run_async(config: dict):
    sc_cfg = config["single_call"]
    model = sc_cfg.get("model", "claude-sonnet-4-6")
    fatigue_limit = int(sc_cfg.get("fatigue_limit", 6))

    ds = config["dataset"]
    items = load_gpqa_diamond(n=int(ds["n"]), seed=int(ds["seed"]))
    out_path = config["logging"]["_output_abs"]
    concurrent = int(config["compute"].get("concurrent", 5))

    prev_meta, prev_records = load_existing(out_path)
    done = {r["question_num"] for r in prev_records
            if r.get("schema_version") == SCHEMA_VERSION}
    if done:
        print(f"{DIM}Resuming: {len(done)} done.{RESET}")

    started_at = datetime.now(timezone.utc)
    meta = prev_meta or make_meta(
        config, started_at, schema_version=SCHEMA_VERSION,
        extra={"model": model, "fatigue_limit": fatigue_limit},
    )

    todo = [(i + 1, it) for i, it in enumerate(items) if (i + 1) not in done]
    print(f"{CYAN}{BOLD}Single-call run · {config['run_key']} · {model} · "
          f"fatigue={fatigue_limit}{RESET}")
    print(f"{DIM}  Will process {len(todo)} / {len(items)}, "
          f"concurrent={concurrent}{RESET}\n")

    sem = asyncio.Semaphore(concurrent)
    results = list(prev_records)
    lock = asyncio.Lock()

    async def _run_one(qnum, item):
        async with sem:
            rec = await run_question(item, qnum, len(items), model, fatigue_limit)
            async with lock:
                results.append(rec)
                results.sort(key=lambda r: r["question_num"])
                save_results(out_path, meta, results)

    await asyncio.gather(*[_run_one(q, it) for q, it in todo])

    meta = finalize_meta(meta, datetime.now(timezone.utc))
    save_results(out_path, meta, sorted(results, key=lambda r: r["question_num"]))
    _print_summary(results, model)
    print(f"\n  Saved to {out_path}\n")


def _print_summary(records, model):
    n = len(records)
    if not n: return
    correct = sum(r["correct"] for r in records)
    hc = [r for r in records if r["outcome"] == "accept_no_change"]
    non_hc = [r for r in records if r["outcome"] != "accept_no_change"]
    hc_prec = 100 * sum(r["correct"] for r in hc) / len(hc) if hc else float("nan")
    non_hc_acc = 100 * sum(r["correct"] for r in non_hc) / len(non_hc) if non_hc else float("nan")
    total_cost = sum(r["cost_usd"] for r in records)
    avg_rounds = sum(r["rounds_used"] for r in records) / n
    print(f"\n{BOLD}{CYAN}  SUMMARY · n={n}{RESET}")
    print(f"  Overall acc:        {correct}/{n} = {100*correct/n:.1f}%")
    print(f"  ANC coverage:       {100*len(hc)/n:.1f}%")
    print(f"  ANC precision:      {hc_prec:.1f}%")
    print(f"  Non-ANC acc:        {non_hc_acc:.1f}%")
    print(f"  Gap:                {hc_prec - non_hc_acc:+.1f}pp")
    print(f"  Avg rounds:         {avg_rounds:.2f}")
    print(f"  Total cost:         ${total_cost:.4f}  (${total_cost/n:.4f}/q)")


def main(config: dict):
    asyncio.run(run_async(config))
