---
name: prover-verifier-codex
description: >-
  Apply Prover–Verifier Deliberation to non-trivial code changes in Codex,
  using GPT-5.6-sol as prover, GPT-5.5 as verifier, and GPT-6-Astra for
  escalation. Use when writing or substantially modifying code, or when
  explicitly asked for PVD review. Skip documentation-only edits, formatting,
  trivial renames, obvious small fixes, and reverts.
---

# Prover–Verifier Deliberation for Codex

Adapt the repository's Claude PVD coding skill to Codex. Follow the user's
scope and instructions. When this skill is selected, use this model pairing
for the PVD loop rather than also running the Claude version.

## Roles and routing

| Role | Exact model ID | Reasoning | Responsibility |
| --- | --- | --- | --- |
| Prover | `gpt-5.6-sol` | `high` | Implement, state claims, respond to challenges, test |
| Verifier | `gpt-5.5` | `high` | Independently inspect code; never edit files |
| Escalator | `gpt-6-astra` | `xhigh` | Resolve the impasse with a redesign or rescope |

This skill explicitly authorizes role subagents for the requested code task.
Use available subagent tools; do not create user-visible tasks as a substitute.
The coordinator owns the loop and transcript. A skill does not change the
coordinator's model by declaring a role in prose.

If the current model is confirmed to be `gpt-5.6-sol`, it may act as prover.
Otherwise delegate implementation to that model and do not silently substitute
the coordinator. Read [references/prover.md](references/prover.md) when delegating.
Always use a separate verifier with [references/verifier.md](references/verifier.md).
Read [references/escalator.md](references/escalator.md) only upon escalation.
Include the relevant reference's actual text in the initial role prompt;
these files are prompts, not registered agent types.

In a Codex runtime exposing `collaboration.spawn_agent`, pass `task_name`,
`model`, `reasoning_effort`, `fork_turns: "none"`, and `message`. For example,
the verifier uses `model: "gpt-5.5"`, `reasoning_effort: "high"` and
`task_name: "pvd_verifier"`. A full-history fork does not support model
overrides in this runtime. Supply task requirements, absolute workspace paths,
role instructions, claims and relevant evidence explicitly. Reuse role agents
with `followup_task` for later rounds; use `send_message` for messages to
running agents. Use another runtime's documented equivalent only if it supports
the exact model selection. Never invent tool parameters or a Claude `Task` call.

Role agents are leaves: they must not invoke this skill recursively or spawn
more agents. The verifier's read-only restriction is an instruction unless
the runtime offers enforceable read-only permissions; do not claim a sandbox
was applied when it was not. Only one role may edit a given file at a time.

If exact model routing or delegation is unavailable, report the specific
limitation and request a model/tool choice before substituting. Do not claim
that this three-model workflow ran when it did not.

## Review loop

Use at most **12 verifier verdicts**, numbered **1–12**, for one coherent
change. Maintain the original requirements, each prover statement, every
verdict, responses, tests and code revisions in the coordinator's transcript.

1. Have the prover implement the requested change and appropriate tests.
   Record the files/diff and 3–6 falsifiable claims about correctness, edge
   cases, minimality and neighboring behavior. Include a short rationale and
   actual test results. Emit a concise `[PROVER round N]` statement in commentary.
2. Freeze the candidate while the verifier reviews it. Send the requirements,
   absolute file paths and current diff (including new files), claims, test
   results, and prior challenges/responses. The verifier should inspect related
   code independently; tests passing alone do not establish correctness.
3. Read the verifier's structured verdict:
   - **Accept:** ANC (Accept + No Change) holds only for the exact candidate
     reviewed, with no code or test edits after that review began. Report
     success only after required checks pass. If edits or new tests are needed,
     have the prover make them and submit the changed candidate in another round
     if fewer than 12 verdicts have been used. At verdict 12, escalate with the
     required edits/tests identified instead; never start round 13.
   - **Challenge:** Have the prover address the specific concern with a fix
     or a supported explanation. It need not change correct code merely to
     satisfy an unfounded challenge. Add meaningful suggested tests when they
     provide missing coverage, run relevant checks, and submit the next round.
   - **Reject:** Have the prover reconsider the implementation or design and
     submit the next round. Track consecutive Reject verdicts; any other
     verdict resets this counter.
4. Escalate after **12 verdicts without ANC**, or **3 consecutive Rejects**,
   whichever happens first. Accept on round 12 still succeeds if ANC holds.
   An Accept invalidated by later edits consumes its round. Do not reset the
   counter when the prover changes designs.

Treat malformed or incomplete verifier output as no verdict: request one
format correction, then report the review failure if it remains unusable.
Do not convert tool failures, missing evidence or silence into acceptance.

Suggested tests are recommendations, not an obligation to add redundant or
implementation-mirroring tests. Explain materially omitted recommendations.
Never add tests after ANC and continue calling the changed candidate accepted.

## Escalation

Give `gpt-6-astra` the full recorded statements and verdicts, requirements,
current code/diff, tests and results, round count, and outstanding challenges.
Pause the prover's writes before handing editing ownership to the escalator.

The escalator either implements a scoped redesign and maps its changes to the
outstanding concerns, or describes the concrete input/scope decision needed.
Allow one escalation pass per loop. Like the Claude workflow, do not restart
the PVD loop after escalation. Run the relevant tests and required checks;
report this outcome as **escalated**, never ANC or independently verified.
If checks fail, return failures to the escalator for one bounded correction
and rerun affected checks. If unresolved, report the failure honestly. A stronger
model's output does not override failed checks or authorize broader scope.

## Completion and skips

Keep the final PVD summary short: outcome (ANC, escalated, blocked or skipped),
verifier rounds used out of 12, important concerns addressed, actual tests run
and remaining limitations. Do not claim test execution from suggested tests.

Skip automatic PVD for documentation-only changes, formatting, obvious small
fixes (roughly ten lines or fewer), trivial renames and reverts. Briefly mention
the skip for a borderline code change. Honor an explicit request to skip review;
do not infer that a request for speed alone waives it. Explicit PVD requests
can include changes that would otherwise be skipped.

## Provenance

Derived from `skills/prover-verifier-code` in the source repository. This is
a coding adaptation of PVD, not a claim of equivalent benchmark performance
for this model pairing. The skill packaging follows the
[official skill guidance](https://learn.chatgpt.com/docs/build-skills).
