"""
Reflexion eval (Shinn et al. 2023).

Protocol:
  trial 0: actor answers at ATTEMPT_TEMP (some stochasticity for diversity)
  recheck: actor answers again at RECHECK_TEMP (independent sample); if it
           agrees with trial 0, the answer is "stable" and we stop. If not:
  reflect: critic writes a verbal reflection on why the answer might be wrong
  trial k: actor answers again at RETRY_TEMP=0.0 with reflection in context
           plus a fresh recheck; repeat until stable, max_trials reached,
           or CONSEC_FAIL_LIMIT consecutive unstable attempts.

HC signal: `final_stable AND not answer_changed` (stable answer that has
not changed since the initial attempt).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import anthropic

from rttr import SCHEMA_VERSION
from rttr.common import (
    CHOICES, MCQItem, TokenUsage,
    cost_usd, finalize_meta,
    load_existing, load_gpqa_diamond, load_prompt, make_meta, save_results,
)


RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
GREEN = "\033[92m"; RED = "\033[91m"; YELLOW = "\033[93m"; CYAN = "\033[96m"


# Hyperparameters (per Shinn et al. defaults; see config to override)
ATTEMPT_TEMP      = 1.0
RECHECK_TEMP      = 1.0
RETRY_TEMP        = 0.0
REFLECT_TEMP      = 0.0
CONSEC_FAIL_LIMIT = 3
MAX_REFLECTIONS   = 3


_client = None
def get_client():
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


async def _api_call(model: str, system: str, messages: list,
                    temperature: float, max_tokens: int = 2000,
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


def _extract_letter(text: str) -> str:
    """
    Extract the actor's final 'Answer: X'.

    The prompt asks for the final answer "on the last line" in the form
    `Answer: X`. We:

    1. Find every ``Answer[:\\s]+(?X)?`` whose letter has a word-boundary
       on its right (so "Answer Choices" won't grab the C of "Choices").
    2. Return the LAST such match — matches the prompt's "last line"
       instruction and survives verbose actors that mention "Answer …"
       elsewhere in their reasoning.
    3. If nothing matches, fall back to the last bare A-D in the last
       300 characters of the response.
    """
    matches = re.findall(r"Answer[:\s]+\(?([A-Da-d])(?=\b)\)?", text)
    if matches:
        return matches[-1].upper()
    fallback = re.findall(r"\b([A-Da-d])\b", text[-300:])
    return fallback[-1].upper() if fallback else "?"


async def run_question(item: MCQItem, qnum: int, total: int,
                       model: str, max_trials: int) -> dict:
    qid = f"Q{qnum}"
    start_ts = time.time()
    mcq_fmt = {"question": item.question, **item.choices}
    attempt_sys = load_prompt("reflexion_attempt_system")
    reflect_sys = load_prompt("reflexion_reflect_system")
    attempt_user = load_prompt("reflexion_attempt_user", **mcq_fmt)

    tokens_total = TokenUsage()
    trials: list[dict] = []
    reflections: list[str] = []
    consec_unstable = 0
    final_stable = False
    initial_answer: Optional[str] = None

    for trial in range(max_trials):
        # ── Actor: produce an answer ────────────────────────────────────────
        if trial == 0:
            actor_messages = [{"role": "user", "content": attempt_user}]
            actor_temp = ATTEMPT_TEMP
        else:
            keep = reflections[-MAX_REFLECTIONS:]
            block = "Previous reflections:\n" + "\n".join(
                f"- {r}" for r in keep) + "\n\n"
            retry_user = load_prompt(
                "reflexion_retry_user",
                reflections_block=block,
                **mcq_fmt,
            )
            actor_messages = [{"role": "user", "content": retry_user}]
            actor_temp = RETRY_TEMP

        a_text, a_usage = await _api_call(
            model, attempt_sys, actor_messages,
            temperature=actor_temp, qid=f"{qid}t{trial}attempt")
        tokens_total = tokens_total + a_usage
        ans = _extract_letter(a_text)

        # ── Recheck: independent attempt ────────────────────────────────────
        r_text, r_usage = await _api_call(
            model, attempt_sys,
            [{"role": "user", "content": attempt_user}],   # fresh, no reflection
            temperature=RECHECK_TEMP, qid=f"{qid}t{trial}recheck")
        tokens_total = tokens_total + r_usage
        recheck_ans = _extract_letter(r_text)

        stable = (ans == recheck_ans and ans in CHOICES)
        if initial_answer is None:
            initial_answer = ans
        consec_unstable = 0 if stable else consec_unstable + 1

        trial_record = {
            "trial":          trial,
            "answer":         ans,
            "recheck_answer": recheck_ans,
            "stable":         stable,
            "attempt_text":   a_text,
            "recheck_text":   r_text,
            "tokens_attempt": a_usage.to_dict(),
            "tokens_recheck": r_usage.to_dict(),
        }
        print(f"  {DIM}[{qid}] trial={trial} ans={ans} recheck={recheck_ans} "
              f"stable={stable}{RESET}", flush=True)

        if stable:
            trials.append(trial_record)
            final_stable = True
            break
        if consec_unstable >= CONSEC_FAIL_LIMIT:
            trials.append(trial_record)
            break
        # ── Critic: produce a reflection ────────────────────────────────────
        reflect_user = load_prompt(
            "reflexion_reflect_user",
            prev_answer=ans, prev_reasoning=a_text,
            **mcq_fmt,
        )
        ref_text, ref_usage = await _api_call(
            model, reflect_sys,
            [{"role": "user", "content": reflect_user}],
            temperature=REFLECT_TEMP, qid=f"{qid}t{trial}reflect")
        tokens_total = tokens_total + ref_usage
        reflections.append(ref_text.strip())
        trial_record["reflection"]       = ref_text
        trial_record["tokens_reflection"] = ref_usage.to_dict()
        trials.append(trial_record)

    final_answer = trials[-1]["answer"]
    answer_changed = (initial_answer is not None and final_answer != initial_answer)
    correct = (final_answer == item.correct_letter)
    cost = cost_usd(tokens_total, model)
    wall = time.time() - start_ts

    icon = GREEN + "✓" + RESET if correct else RED + "✗" + RESET
    print(f"  {icon} {qid}: final={BOLD}{final_answer}{RESET} "
          f"correct={item.correct_letter} stable={final_stable} "
          f"changed={answer_changed} trials={len(trials)} cost=${cost:.4f}",
          flush=True)

    return {
        "question_num":   qnum,
        "subdomain":      item.subdomain,
        "domain":         item.domain,
        "question":       item.question,
        "choices":        item.choices,
        "correct_letter": item.correct_letter,
        "initial_answer": initial_answer,
        "final_answer":   final_answer,
        "correct":        correct,
        "final_stable":   final_stable,
        "answer_changed": answer_changed,
        "trials_used":    len(trials),
        "max_trials":     max_trials,
        "trials":         trials,   # verbose transcript per trial
        "tokens":         tokens_total.to_dict(),
        "cost_usd":       round(cost, 6),
        "wall_seconds":   round(wall, 2),
        "model":          model,
        "schema_version": SCHEMA_VERSION,
    }


async def run_async(config: dict):
    rfl = config["reflexion"]
    model = rfl["model"]
    max_trials = int(rfl.get("max_trials", 5))

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
        extra={"model": model, "max_trials": max_trials},
    )

    todo = [(i + 1, it) for i, it in enumerate(items) if (i + 1) not in done]
    print(f"{CYAN}{BOLD}Reflexion run · {config['run_key']} · {model} · "
          f"max_trials={max_trials}{RESET}")
    print(f"{DIM}  Will process {len(todo)} / {len(items)}, concurrent={concurrent}{RESET}\n")

    sem = asyncio.Semaphore(concurrent)
    results = list(prev_records)
    lock = asyncio.Lock()

    async def _run_one(qnum, item):
        async with sem:
            rec = await run_question(item, qnum, len(items), model, max_trials)
            async with lock:
                results.append(rec)
                results.sort(key=lambda r: r["question_num"])
                save_results(out_path, meta, results)

    await asyncio.gather(*[_run_one(q, it) for q, it in todo])

    meta = finalize_meta(meta, datetime.now(timezone.utc))
    save_results(out_path, meta, sorted(results, key=lambda r: r["question_num"]))
    _print_summary(results, model)
    print(f"\n  Saved to {out_path}\n")


def _print_summary(records: list[dict], model: str):
    total = len(records)
    if not total:
        return
    correct = sum(r["correct"] for r in records)
    hc = [r for r in records if r["final_stable"] and not r["answer_changed"]]
    non_hc = [r for r in records if not (r["final_stable"] and not r["answer_changed"])]
    hc_cov = 100 * len(hc) / total
    hc_prec = 100 * sum(r["correct"] for r in hc) / len(hc) if hc else float("nan")
    non_hc_acc = 100 * sum(r["correct"] for r in non_hc) / len(non_hc) if non_hc else float("nan")
    total_cost = sum(r["cost_usd"] for r in records)
    avg_trials = sum(r["trials_used"] for r in records) / total
    print(f"\n{BOLD}{CYAN}  SUMMARY · n={total}{RESET}")
    print(f"  Overall acc:    {correct}/{total} = {100*correct/total:.1f}%")
    print(f"  HC coverage:    {hc_cov:.1f}%   (stable & unchanged)")
    print(f"  HC precision:   {hc_prec:.1f}%")
    print(f"  Non-HC acc:     {non_hc_acc:.1f}%")
    print(f"  Gap:            {hc_prec - non_hc_acc:+.1f}pp")
    print(f"  Avg trials:     {avg_trials:.2f}")
    print(f"  Total cost:     ${total_cost:.4f}  (${total_cost/total:.4f}/q)")


def main(config: dict):
    asyncio.run(run_async(config))
