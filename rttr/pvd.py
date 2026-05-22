"""
PVD evaluation — supports four config-driven variants.

Variants (selected by config flags in the `pvd:` block):

    standard   max_attempts=1, min_challenges=0, self_play=false
    min1       max_attempts=1, min_challenges=1, self_play=false
    self       max_attempts=1, min_challenges=1, self_play=true
    retry      max_attempts=5, min_challenges=0, self_play=false

The protocol is a single function; the variants differ only in how it
parameterizes:
  - which verifier system prompt to load (standard vs. min1)
  - whether the verifier model == prover model (self-play)
  - how many attempts to spend per question

Output schema: rttr-v1 (see README.md).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic

from rttr import SCHEMA_VERSION
from rttr.common import (
    CHOICES, MCQItem, TokenUsage,
    cost_usd, extract_json, finalize_meta, format_mcq,
    load_existing, load_gpqa_diamond, load_prompt, make_meta, save_results,
)


# ── ANSI ─────────────────────────────────────────────────────────────────────
RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
GREEN = "\033[92m"; RED = "\033[91m"; YELLOW = "\033[93m"
BLUE  = "\033[94m"; CYAN = "\033[96m"; MAGENTA = "\033[95m"


# ── Client (lazily constructed for testability) ──────────────────────────────
_client = None
def get_client():
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


# ── Anthropic API wrapper with retries ───────────────────────────────────────

async def _api_call(model: str, system: str, messages: list,
                    max_tokens: int = 2000,
                    thinking_budget: int = 0,
                    qid: str = "", role: str = "",
                    retries: int = 3) -> tuple[str, TokenUsage]:
    """One API call, returning (raw_text, TokenUsage)."""
    client = get_client()
    kwargs = dict(model=model, max_tokens=max_tokens, system=system, messages=messages)
    if thinking_budget > 0:
        # max_tokens must be > thinking budget; thinking output bills as output.
        kwargs["max_tokens"] = max(max_tokens, thinking_budget + 1024)
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
    for attempt in range(retries):
        try:
            resp = await client.messages.create(**kwargs)
            text = ""
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    text += block.text
                elif hasattr(block, "text"):
                    text += block.text
            usage = TokenUsage.from_anthropic(resp.usage)
            return text, usage
        except Exception as e:
            print(f"{RED}  [{qid}/{role}] API error (try {attempt+1}): {e}{RESET}", flush=True)
            await asyncio.sleep(2 ** attempt)
    return "", TokenUsage()


# ── Prover / verifier responses ──────────────────────────────────────────────

@dataclass
class ProverResponse:
    answer: str
    statement: str
    subclaims: list
    reasoning: str
    raw: str
    tokens: TokenUsage

@dataclass
class VerifierResponse:
    verdict: str
    reasoning: str
    challenge: Optional[str]
    challenged_claim: Optional[str]
    raw: str
    tokens: TokenUsage


async def call_prover(messages: list, prover_model: str, thinking_budget: int,
                      qid: str) -> ProverResponse:
    system = load_prompt("pvd_prover_system")
    for retry in range(3):
        raw, usage = await _api_call(prover_model, system, messages,
                                     max_tokens=2000,
                                     thinking_budget=thinking_budget,
                                     qid=qid, role="P")
        data = extract_json(raw)
        if data:
            ans = data.get("answer", "").strip().upper()[:1]
            if ans in CHOICES:
                return ProverResponse(
                    answer=ans,
                    statement=data.get("statement", ""),
                    subclaims=data.get("subclaims", []),
                    reasoning=data.get("reasoning", ""),
                    raw=raw, tokens=usage,
                )
        print(f"{YELLOW}  [{qid}] invalid prover JSON (try {retry+1}){RESET}", flush=True)
    return ProverResponse("?", "", [], "", raw, usage)


async def call_verifier(messages: list, verifier_model: str,
                        verifier_system_prompt: str, qid: str) -> VerifierResponse:
    raw, usage = await _api_call(verifier_model, verifier_system_prompt, messages,
                                 max_tokens=600, thinking_budget=0,
                                 qid=qid, role="V")
    data = extract_json(raw)
    if data:
        return VerifierResponse(
            verdict=data.get("verdict", "Reject"),
            reasoning=data.get("reasoning", ""),
            challenge=data.get("challenge"),
            challenged_claim=data.get("challenged_claim"),
            raw=raw, tokens=usage,
        )
    return VerifierResponse("Reject", raw, None, None, raw, usage)


# ── One deliberation (single attempt) ────────────────────────────────────────

@dataclass
class AttemptRecord:
    attempt_num: int
    initial_answer: str
    final_answer: str
    answer_changes: int
    rounds_used: int
    final_verdict: str          # Accept | Reject | Reject (fatigue)
    outcome: str                # accept_no_change | accept_changed | reject | fatigue
    transcript: list = field(default_factory=list)
    tokens_prover: TokenUsage = field(default_factory=TokenUsage)
    tokens_verifier: TokenUsage = field(default_factory=TokenUsage)


def _prior_context(attempts: list[AttemptRecord]) -> str:
    """Build the retry prefix from previous AttemptRecords."""
    lines = []
    for rec in attempts:
        lines.append(f"Attempt {rec.attempt_num}:")
        lines.append(f"  Initial answer: {rec.initial_answer}")
        if rec.answer_changes > 0:
            lines.append(f"  Answer changes: {rec.answer_changes} (ended on {rec.final_answer})")
        outcome_desc = {
            "accept_changed": "Verifier Accepted but you changed your answer during deliberation.",
            "reject":         "Verifier Rejected your reasoning.",
            "fatigue":        "Deliberation hit the round limit without reaching Accept.",
        }.get(rec.outcome, rec.outcome)
        lines.append(f"  Outcome: {outcome_desc}")
        # Last verifier challenge text, if available
        for turn in reversed(rec.transcript):
            if turn["role"] == "verifier" and turn["verdict"] in ("Reject", "Challenge"):
                msg = turn.get("challenge") or turn.get("reasoning") or ""
                if msg:
                    lines.append(f"  Key verifier issue: \"{msg[:300]}\"")
                break
        lines.append("")
    return load_prompt("pvd_retry_prior_context",
                       num_prior_attempts=len(attempts),
                       prior_summary="\n".join(lines))


async def run_attempt(
    item: MCQItem, qid: str, attempt_num: int,
    prover_model: str, verifier_model: str,
    verifier_system_prompt: str,
    thinking_budget: int,
    fatigue_limit: int,
    min_challenges: int,
    prior_context_text: Optional[str],
) -> AttemptRecord:
    """Run one prover+verifier deliberation. Returns an AttemptRecord."""
    mcq_text = format_mcq(item)
    first_user = (prior_context_text + mcq_text) if prior_context_text else mcq_text

    prover_history = [{"role": "user", "content": first_user}]
    verifier_history: list[dict] = []
    transcript: list[dict] = []

    round_num = 0
    answer_changes = 0
    last_valid = "?"
    challenges_so_far = 0
    tokens_p = TokenUsage()
    tokens_v = TokenUsage()

    # ── Initial prover call (turn 0) ─────────────────────────────────────────
    pr = await call_prover(prover_history, prover_model, thinking_budget, qid)
    tokens_p = tokens_p + pr.tokens
    transcript.append({
        "role": "prover", "turn": 0,
        "answer": pr.answer, "statement": pr.statement,
        "subclaims": pr.subclaims, "reasoning": pr.reasoning,
        "text": pr.raw,
        "tokens_in": pr.tokens.input,
        "tokens_out": pr.tokens.output,
        "thinking_tokens": pr.tokens.thinking,
    })
    prover_history.append({"role": "assistant", "content": json.dumps({
        "answer": pr.answer, "statement": pr.statement,
        "subclaims": pr.subclaims, "reasoning": pr.reasoning,
    })})

    initial_answer = pr.answer
    last_valid = pr.answer

    # First verifier user message
    verifier_history.append({"role": "user", "content": load_prompt(
        "pvd_verifier_first_user",
        question=mcq_text, prover_answer=pr.answer,
        prover_statement=pr.statement,
        prover_subclaims_json=json.dumps(pr.subclaims, indent=2),
        prover_reasoning=pr.reasoning,
    )})

    print(f"  {DIM}[{qid}] attempt={attempt_num} initial={initial_answer}{RESET}", flush=True)

    # ── Verifier loop ────────────────────────────────────────────────────────
    while True:
        round_num += 1
        vr = await call_verifier(verifier_history, verifier_model,
                                 verifier_system_prompt, qid)
        tokens_v = tokens_v + vr.tokens
        # min-1 enforcement: if config requires ≥1 challenge and verifier wants
        # to Accept before that, downgrade to Challenge with a fallback probe.
        # The system prompt should already prevent this, but we belt-and-suspender.
        if min_challenges > 0 and challenges_so_far < min_challenges and vr.verdict == "Accept":
            vr = VerifierResponse(
                verdict="Challenge",
                reasoning=(vr.reasoning + " [forced: protocol requires ≥1 challenge]").strip(),
                challenge=vr.challenge or "Please justify your single most critical sub-claim rigorously.",
                challenged_claim=vr.challenged_claim or (pr.subclaims[0] if pr.subclaims else "the first sub-claim"),
                raw=vr.raw, tokens=vr.tokens,
            )
        if vr.verdict == "Challenge":
            challenges_so_far += 1
        transcript.append({
            "role": "verifier", "turn": round_num,
            "verdict": vr.verdict, "reasoning": vr.reasoning,
            "challenge": vr.challenge, "challenged_claim": vr.challenged_claim,
            "text": vr.raw,
            "tokens_in": vr.tokens.input,
            "tokens_out": vr.tokens.output,
            "thinking_tokens": vr.tokens.thinking,
        })
        verifier_history.append({"role": "assistant", "content": json.dumps({
            "verdict": vr.verdict, "reasoning": vr.reasoning,
            "challenge": vr.challenge, "challenged_claim": vr.challenged_claim,
        })})

        icon = {"Accept": "✓", "Reject": "✗", "Challenge": "?"}.get(vr.verdict, "·")
        print(f"  {DIM}[{qid}] A{attempt_num}R{round_num}: ans={pr.answer} "
              f"verdict={icon}{vr.verdict}{RESET}", flush=True)

        if vr.verdict in ("Accept", "Reject"):
            outcome = ("accept_no_change" if vr.verdict == "Accept" and answer_changes == 0
                       else "accept_changed" if vr.verdict == "Accept"
                       else "reject")
            return AttemptRecord(
                attempt_num=attempt_num,
                initial_answer=initial_answer,
                final_answer=pr.answer,
                answer_changes=answer_changes,
                rounds_used=round_num,
                final_verdict=vr.verdict,
                outcome=outcome,
                transcript=transcript,
                tokens_prover=tokens_p,
                tokens_verifier=tokens_v,
            )

        if round_num >= fatigue_limit:
            print(f"  {YELLOW}[{qid}] A{attempt_num}: fatigue reached{RESET}", flush=True)
            return AttemptRecord(
                attempt_num=attempt_num,
                initial_answer=initial_answer,
                final_answer=pr.answer,
                answer_changes=answer_changes,
                rounds_used=round_num,
                final_verdict="Reject (fatigue)",
                outcome="fatigue",
                transcript=transcript,
                tokens_prover=tokens_p,
                tokens_verifier=tokens_v,
            )

        # ── Challenge: ask prover to respond ─────────────────────────────────
        challenge_msg = load_prompt(
            "pvd_prover_challenge_response",
            challenged_claim=vr.challenged_claim or "(none)",
            verifier_question=vr.challenge or "(no challenge text)",
        )
        prover_history.append({"role": "user", "content": challenge_msg})
        pr = await call_prover(prover_history, prover_model, thinking_budget, qid)
        tokens_p = tokens_p + pr.tokens
        transcript.append({
            "role": "prover", "turn": round_num,
            "answer": pr.answer, "statement": pr.statement,
            "subclaims": pr.subclaims, "reasoning": pr.reasoning,
            "text": pr.raw,
            "tokens_in": pr.tokens.input,
            "tokens_out": pr.tokens.output,
            "thinking_tokens": pr.tokens.thinking,
        })
        prover_history.append({"role": "assistant", "content": json.dumps({
            "answer": pr.answer, "statement": pr.statement,
            "subclaims": pr.subclaims, "reasoning": pr.reasoning,
        })})

        if pr.answer not in CHOICES:
            pr.answer = last_valid
        else:
            if pr.answer != last_valid and last_valid in CHOICES:
                answer_changes += 1
                print(f"  {MAGENTA}[{qid}] A{attempt_num}: answer "
                      f"{last_valid}→{pr.answer}{RESET}", flush=True)
            last_valid = pr.answer

        verifier_history.append({"role": "user", "content": load_prompt(
            "pvd_verifier_next_round",
            challenged_claim=vr.challenged_claim or "(none)",
            prover_answer=pr.answer,
            prover_statement=pr.statement,
            prover_subclaims_json=json.dumps(pr.subclaims, indent=2),
            prover_reasoning=pr.reasoning,
            round_num=round_num,
            fatigue_limit=fatigue_limit,
        )})


# ── Per-question driver (handles retries) ────────────────────────────────────

def _majority_vote(attempts: list[AttemptRecord]) -> str:
    votes = Counter(a.final_answer for a in attempts if a.final_answer in CHOICES)
    if not votes:
        return "?"
    return votes.most_common(1)[0][0]


async def run_question(item: MCQItem, qnum: int, total: int,
                       prover_model: str, verifier_model: str,
                       verifier_system_prompt: str,
                       thinking_budget: int,
                       fatigue_limit: int,
                       max_attempts: int,
                       min_challenges: int) -> dict:
    qid = f"Q{qnum}"
    start_ts = time.time()
    attempts: list[AttemptRecord] = []
    final_attempt: Optional[AttemptRecord] = None

    print(f"\n{BOLD}{MAGENTA}{'▓'*60}{RESET}", flush=True)
    print(f"{BOLD}{MAGENTA}  {qid}/{total} · {item.subdomain}{RESET}", flush=True)

    for attempt_num in range(1, max_attempts + 1):
        prior = _prior_context(attempts) if attempts else None
        rec = await run_attempt(
            item, qid, attempt_num,
            prover_model, verifier_model, verifier_system_prompt,
            thinking_budget, fatigue_limit, min_challenges, prior,
        )
        attempts.append(rec)
        print(f"  {DIM}[{qid}] A{attempt_num}: outcome={rec.outcome} "
              f"ans={rec.final_answer} chg={rec.answer_changes} "
              f"rounds={rec.rounds_used}{RESET}", flush=True)
        if rec.outcome == "accept_no_change":
            final_attempt = rec
            break

    # Roll up
    total_rounds = sum(a.rounds_used for a in attempts)
    tokens_p = TokenUsage()
    tokens_v = TokenUsage()
    for a in attempts:
        tokens_p = tokens_p + a.tokens_prover
        tokens_v = tokens_v + a.tokens_verifier

    if final_attempt is not None:
        final_answer = final_attempt.final_answer
        final_verdict = "Accept"
        outcome = "accept_no_change"
    elif max_attempts == 1:
        # No retry: report whatever the single attempt produced
        last = attempts[-1]
        final_answer = last.final_answer
        final_verdict = last.final_verdict
        outcome = last.outcome
    else:
        # Retry exhausted: majority vote
        final_answer = _majority_vote(attempts)
        final_verdict = "Reject (majority_vote)"
        outcome = "majority_vote"

    correct = (final_answer == item.correct_letter)
    cost = cost_usd(tokens_p, prover_model) + cost_usd(tokens_v, verifier_model)
    wall = time.time() - start_ts

    icon = GREEN + "✓" + RESET if correct else RED + "✗" + RESET
    print(f"  {icon} {qid}: ans={BOLD}{final_answer}{RESET} "
          f"correct={item.correct_letter} attempts={len(attempts)} "
          f"rounds={total_rounds} cost=${cost:.4f}", flush=True)

    return {
        "question_num":     qnum,
        "subdomain":        item.subdomain,
        "domain":           item.domain,
        "question":         item.question,
        "choices":          item.choices,
        "correct_letter":   item.correct_letter,
        "prover_answer":    final_answer,
        "correct":          correct,
        "final_verdict":    final_verdict,
        "outcome":          outcome,
        "num_attempts":     len(attempts),
        "total_rounds":     total_rounds,
        "max_attempts":     max_attempts,
        "fatigue_limit":    fatigue_limit,
        "min_challenges":   min_challenges,
        "attempts": [
            {
                "attempt_num":    a.attempt_num,
                "initial_answer": a.initial_answer,
                "final_answer":   a.final_answer,
                "answer_changes": a.answer_changes,
                "rounds_used":    a.rounds_used,
                "final_verdict":  a.final_verdict,
                "outcome":        a.outcome,
                "transcript":     a.transcript,
                "tokens": {
                    "prover":   a.tokens_prover.to_dict(),
                    "verifier": a.tokens_verifier.to_dict(),
                },
            } for a in attempts
        ],
        "tokens": {
            "prover":   tokens_p.to_dict(),
            "verifier": tokens_v.to_dict(),
        },
        "cost_usd":         round(cost, 6),
        "wall_seconds":     round(wall, 2),
        "prover_model":     prover_model,
        "verifier_model":   verifier_model,
        "thinking_budget_tokens": thinking_budget,
        "schema_version":   SCHEMA_VERSION,
    }


# ── Main entry point ─────────────────────────────────────────────────────────

async def run_async(config: dict):
    pvd_cfg = config["pvd"]
    prover_model       = pvd_cfg["prover_model"]
    verifier_model     = pvd_cfg.get("verifier_model") or prover_model
    self_play          = bool(pvd_cfg.get("self_play", False))
    if self_play:
        verifier_model = prover_model
    thinking_budget    = int(pvd_cfg.get("thinking_budget_tokens", 0))
    fatigue_limit      = int(pvd_cfg.get("fatigue_limit", 12))
    max_attempts       = int(pvd_cfg.get("max_attempts", 1))
    min_challenges     = int(pvd_cfg.get("min_challenges", 0))

    # Pick verifier system prompt
    verifier_system_prompt = load_prompt(
        "pvd_verifier_system_min1" if min_challenges > 0
        else "pvd_verifier_system"
    )

    # Dataset
    ds = config["dataset"]
    items = load_gpqa_diamond(n=int(ds["n"]), seed=int(ds["seed"]))

    out_path = config["logging"]["_output_abs"]
    concurrent = int(config["compute"].get("concurrent", 5))

    # Resume: skip already-completed question_nums (matching schema)
    prev_meta, prev_records = load_existing(out_path)
    done_qnums = {r["question_num"] for r in prev_records
                  if r.get("schema_version") == SCHEMA_VERSION}
    if done_qnums:
        print(f"{DIM}Resuming: {len(done_qnums)} questions already done.{RESET}")

    # Build meta
    started_at = datetime.now(timezone.utc)
    meta = prev_meta or make_meta(
        config, started_at, schema_version=SCHEMA_VERSION,
        extra={"prover_model": prover_model, "verifier_model": verifier_model,
               "thinking_budget_tokens": thinking_budget,
               "fatigue_limit": fatigue_limit,
               "max_attempts": max_attempts,
               "min_challenges": min_challenges},
    )

    # Filter items to those needing processing
    todo = [(i + 1, it) for i, it in enumerate(items)
            if (i + 1) not in done_qnums]
    print(f"{CYAN}{BOLD}PVD run · {config['run_key']} · prover={prover_model} verifier={verifier_model}"
          f" thinking={thinking_budget} max_attempts={max_attempts}"
          f" min_challenges={min_challenges}{RESET}")
    print(f"{DIM}  Will process {len(todo)} / {len(items)} questions, concurrent={concurrent}{RESET}\n")

    sem = asyncio.Semaphore(concurrent)
    results: list[dict] = list(prev_records)
    lock = asyncio.Lock()

    async def _run_one(qnum, item):
        async with sem:
            rec = await run_question(
                item, qnum, len(items),
                prover_model, verifier_model, verifier_system_prompt,
                thinking_budget, fatigue_limit, max_attempts, min_challenges,
            )
            async with lock:
                results.append(rec)
                results.sort(key=lambda r: r["question_num"])
                save_results(out_path, meta, results)

    await asyncio.gather(*[_run_one(q, it) for q, it in todo])

    # Final meta + summary
    meta = finalize_meta(meta, datetime.now(timezone.utc))
    save_results(out_path, meta, sorted(results, key=lambda r: r["question_num"]))
    _print_summary(results, prover_model, verifier_model)
    print(f"\n  Saved to {out_path}\n")


def _print_summary(records: list[dict], prover_model: str, verifier_model: str):
    total = len(records)
    if total == 0:
        return
    correct = sum(r["correct"] for r in records)
    anc = [r for r in records if r["outcome"] == "accept_no_change"]
    non_anc = [r for r in records if r["outcome"] != "accept_no_change"]
    hc_cov = 100 * len(anc) / total
    hc_prec = 100 * sum(r["correct"] for r in anc) / len(anc) if anc else float("nan")
    non_hc_acc = 100 * sum(r["correct"] for r in non_anc) / len(non_anc) if non_anc else float("nan")
    avg_attempts = sum(r["num_attempts"] for r in records) / total
    avg_rounds = sum(r["total_rounds"] for r in records) / total
    total_cost = sum(r["cost_usd"] for r in records)

    print(f"\n{BOLD}{CYAN}{'═'*65}{RESET}")
    print(f"{BOLD}{CYAN}  SUMMARY · n={total}{RESET}")
    print(f"  Overall accuracy:  {correct}/{total} = {100*correct/total:.1f}%")
    print(f"  ANC coverage:      {len(anc)}/{total} = {hc_cov:.1f}%")
    print(f"  ANC precision:     {hc_prec:.1f}%")
    print(f"  Non-ANC accuracy:  {non_hc_acc:.1f}%")
    print(f"  Gap:               {hc_prec - non_hc_acc:+.1f}pp")
    print(f"  Avg attempts:      {avg_attempts:.2f}")
    print(f"  Avg total rounds:  {avg_rounds:.2f}")
    print(f"  Total cost:        ${total_cost:.4f}  (${total_cost/total:.4f}/question)")


def main(config: dict):
    asyncio.run(run_async(config))
