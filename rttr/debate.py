"""
Multi-Agent Debate eval (Du et al. 2024).

Each question runs `num_agents` agents in parallel for `1 + num_rounds`
turns each. Round 0: every agent answers independently at INIT_TEMP.
Subsequent rounds: each agent sees the previous responses of the other
agents and updates its answer at DEBATE_TEMP.

HC signal: full consensus (all agents agree on the final answer).
Final answer: majority vote across last-round answers.
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
    cost_usd, finalize_meta, format_mcq,
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
    matches = re.findall(r"\(([A-Da-d])\)", text)
    if matches:
        return matches[-1].upper()
    m = re.search(r"\b([A-Da-d])\b", text[-300:])
    return m.group(1).upper() if m else "?"


def _majority_vote(letters: list[str]) -> str:
    valid = [l for l in letters if l in CHOICES]
    if not valid:
        return "?"
    top = Counter(valid).most_common()
    max_count = top[0][1]
    leaders = sorted(l for l, c in top if c == max_count)
    return leaders[0]


async def run_question(item: MCQItem, qnum: int, total: int,
                       model: str, num_agents: int, num_rounds: int,
                       init_temp: float, debate_temp: float) -> dict:
    qid = f"Q{qnum}"
    start_ts = time.time()
    mcq_fmt = {"question": item.question, **item.choices}
    system = load_prompt("debate_system")

    histories: list[list[dict]] = [[] for _ in range(num_agents)]
    rounds_log: list[list[dict]] = []
    tokens_total = TokenUsage()

    # ── Round 0: independent ─────────────────────────────────────────────────
    init_user = load_prompt("debate_agent_initial", **mcq_fmt)
    for i in range(num_agents):
        histories[i].append({"role": "user", "content": init_user})
    r0 = await asyncio.gather(*[
        _api_call(model, system, histories[i], temperature=init_temp,
                  qid=f"{qid}a{i+1}r0")
        for i in range(num_agents)
    ])
    round_log = []
    answers = []
    for i, (text, usage) in enumerate(r0):
        tokens_total = tokens_total + usage
        ans = _extract_letter(text)
        answers.append(ans)
        histories[i].append({"role": "assistant", "content": text})
        round_log.append({
            "agent": i + 1, "round": 0, "answer": ans, "text": text,
            "tokens_in": usage.input, "tokens_out": usage.output,
            "thinking_tokens": usage.thinking,
        })
    rounds_log.append(round_log)
    print(f"  {DIM}[{qid}] R0 answers: {answers}{RESET}", flush=True)

    # ── Rounds 1..num_rounds: see peers, update ──────────────────────────────
    for rnd in range(1, num_rounds + 1):
        # Build "other_responses" for each agent
        prev_texts = [rounds_log[-1][i]["text"] for i in range(num_agents)]
        prompts = []
        for i in range(num_agents):
            others = []
            for j in range(num_agents):
                if j == i:
                    continue
                others.append(f"One agent solution:\n```{prev_texts[j]}```")
            other_responses = "\n\n".join(others)
            user_msg = load_prompt("debate_agent_update", other_responses=other_responses)
            histories[i].append({"role": "user", "content": user_msg})
            prompts.append(histories[i])
        results = await asyncio.gather(*[
            _api_call(model, system, prompts[i], temperature=debate_temp,
                      qid=f"{qid}a{i+1}r{rnd}")
            for i in range(num_agents)
        ])
        round_log = []
        answers = []
        for i, (text, usage) in enumerate(results):
            tokens_total = tokens_total + usage
            ans = _extract_letter(text)
            answers.append(ans)
            histories[i].append({"role": "assistant", "content": text})
            round_log.append({
                "agent": i + 1, "round": rnd, "answer": ans, "text": text,
                "tokens_in": usage.input, "tokens_out": usage.output,
                "thinking_tokens": usage.thinking,
            })
        rounds_log.append(round_log)
        print(f"  {DIM}[{qid}] R{rnd} answers: {answers}{RESET}", flush=True)

    # ── Aggregate ────────────────────────────────────────────────────────────
    last_round_answers = [t["answer"] for t in rounds_log[-1]]
    final_answer = _majority_vote(last_round_answers)
    consensus_reached = len(set(last_round_answers)) == 1 and final_answer != "?"
    correct = (final_answer == item.correct_letter)
    cost = cost_usd(tokens_total, model)
    wall = time.time() - start_ts

    icon = GREEN + "✓" + RESET if correct else RED + "✗" + RESET
    print(f"  {icon} {qid}: final={BOLD}{final_answer}{RESET} "
          f"correct={item.correct_letter} consensus={consensus_reached} "
          f"cost=${cost:.4f}", flush=True)

    return {
        "question_num":      qnum,
        "subdomain":         item.subdomain,
        "domain":            item.domain,
        "question":          item.question,
        "choices":           item.choices,
        "correct_letter":    item.correct_letter,
        "final_answer":      final_answer,
        "correct":           correct,
        "consensus_reached": consensus_reached,
        "agent_answers_by_round": [[t["answer"] for t in r] for r in rounds_log],
        "rounds":            rounds_log,    # verbose transcript
        "tokens":            tokens_total.to_dict(),
        "cost_usd":          round(cost, 6),
        "wall_seconds":      round(wall, 2),
        "model":             model,
        "num_agents":        num_agents,
        "num_rounds":        num_rounds,
        "init_temp":         init_temp,
        "debate_temp":       debate_temp,
        "schema_version":    SCHEMA_VERSION,
    }


async def run_async(config: dict):
    d = config["debate"]
    model = d["model"]
    num_agents = int(d.get("num_agents", 3))
    num_rounds = int(d.get("num_rounds", 2))
    init_temp = float(d.get("init_temp", 1.0))
    debate_temp = float(d.get("debate_temp", 0.0))

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
        extra={"model": model, "num_agents": num_agents,
               "num_rounds": num_rounds,
               "init_temp": init_temp, "debate_temp": debate_temp},
    )

    todo = [(i + 1, it) for i, it in enumerate(items) if (i + 1) not in done]
    print(f"{CYAN}{BOLD}Debate run · {config['run_key']} · {model} · "
          f"{num_agents} agents × {num_rounds} rounds{RESET}")
    print(f"{DIM}  Will process {len(todo)} / {len(items)}, concurrent={concurrent}{RESET}\n")

    sem = asyncio.Semaphore(concurrent)
    results = list(prev_records)
    lock = asyncio.Lock()

    async def _run_one(qnum, item):
        async with sem:
            rec = await run_question(item, qnum, len(items),
                                     model, num_agents, num_rounds,
                                     init_temp, debate_temp)
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
    consensus = [r for r in records if r["consensus_reached"]]
    non_cons  = [r for r in records if not r["consensus_reached"]]
    hc_cov = 100 * len(consensus) / total
    hc_prec = 100 * sum(r["correct"] for r in consensus) / len(consensus) if consensus else float("nan")
    non_hc_acc = 100 * sum(r["correct"] for r in non_cons) / len(non_cons) if non_cons else float("nan")
    total_cost = sum(r["cost_usd"] for r in records)
    print(f"\n{BOLD}{CYAN}  SUMMARY · n={total}{RESET}")
    print(f"  Overall acc:      {correct}/{total} = {100*correct/total:.1f}%")
    print(f"  Consensus cov:    {hc_cov:.1f}%")
    print(f"  Consensus prec:   {hc_prec:.1f}%")
    print(f"  Non-consensus:    {non_hc_acc:.1f}%")
    print(f"  Gap:              {hc_prec - non_hc_acc:+.1f}pp")
    print(f"  Total cost:       ${total_cost:.4f}  (${total_cost/total:.4f}/q)")


def main(config: dict):
    asyncio.run(run_async(config))
