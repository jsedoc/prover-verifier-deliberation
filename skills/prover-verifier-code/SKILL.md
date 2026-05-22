---
name: prover-verifier-code
description: |
  Apply the Prover–Verifier Deliberation (PVD) protocol from the paper to every
  non-trivial piece of code you write or modify. You are the Prover (Sonnet 4.6
  with high reasoning); delegate to the bundled `pvd-verifier` subagent
  (Haiku 4.5, read-only) to challenge questionable code, comment on minimality
  and complexity, and propose unit tests. Iterate up to 12 rounds (fatigue
  limit); if no ANC (Accept + No Change) verdict is reached, escalate to the
  bundled `pvd-opus-escalator` subagent (Opus 4.7, extra high) for a fresh
  design pass.

  Trigger this skill whenever you are about to write or substantially modify
  code — i.e. you would otherwise call Edit/Write/NotebookEdit on a code file
  (.py, .ts, .tsx, .js, .jsx, .go, .rs, .java, .c, .cc, .cpp, .h, .rb, .sh,
  .swift, .kt, .scala, .lua, .ex, .ml, etc.). Skip for: docs-only changes
  (.md, .txt, .rst), pure formatting/whitespace, single-line typo fixes,
  trivial renames, and reverts. If the change is under ~10 lines and is
  obviously correct, you may skip the protocol but mention that you did so.
---

# Prover–Verifier Deliberation for Code

You are the **Prover** (main Claude Code session, Sonnet 4.6, high reasoning).
After producing or modifying code, run the following loop until ANC or
fatigue.

> **Prerequisite.** This skill invokes two subagents by name: `pvd-verifier`
> (Haiku, read-only) and `pvd-opus-escalator` (Opus, write). They ship
> alongside this SKILL.md under `agents/`. If `/agents` does not list them,
> the install was incomplete — run `./skills/install.sh prover-verifier-code`
> from the source repo before continuing.

## Concepts

- **ANC (Accept + No Change)** — the verifier issues `Accept` *and* the prover
  did not modify the code in response to the most recent verifier challenge.
  This is the success condition. ANC is the same notion as in the paper.
- **Fatigue limit = 12** — maximum prover↔verifier rounds per code change.
- **Escalation** — on fatigue (or persistent `Reject`), hand the full
  transcript to the Opus subagent for a fresh design.

## Protocol

### Step 1 — Prover statement (round 0)

After writing the code, produce a Prover statement *in your own response* (not
a tool call). Keep it short:

```
[PROVER round 0]
Files: <list of files touched>
Diff summary: <one-line description of the change>
Sub-claims:
  1. <atomic, independently-checkable claim about correctness>
  2. <claim about edge-case coverage>
  3. <claim about minimality / why this design over alternatives>
  4. <claim about no regressions to neighbouring code>
  (3–6 sub-claims; each must be falsifiable)
Reasoning: <2–3 sentences of justification>
```

### Step 2 — Invoke the verifier subagent

Use the `Task` tool with `subagent_type="pvd-verifier"`. The subagent's system
prompt (in `agents/pvd-verifier.md`) handles the verdict logic; your job here
is to package up the round context.

```
Task(
  subagent_type = "pvd-verifier",
  description   = "PVD verify round 0",
  prompt        = <Round context — see below>
)
```

#### Round context to pass

```
Round: <k>

Code under review (paths + contents, or unified diff):
<CODE>

Prover sub-claims:
<SUB-CLAIMS — same list as the Prover statement above>

Prior rounds (for k > 0):
<concise list of (round, challenge issued, prover response) tuples>
```

Do **not** repeat the verifier's job description in this prompt — that lives
in the subagent's own system prompt. Just pass the round context.

### Step 3 — Handle the verdict

The verifier returns a single fenced JSON block:

```json
{
  "verdict": "Accept" | "Reject" | "Challenge",
  "reasoning": "...",
  "challenge": "...",
  "challenged_claim": "...",
  "style_notes": "...",
  "suggested_tests": [{"name": "...", "input": "...", "expected": "..."}]
}
```

Parse it. Then:

- **`Accept`** AND the code is unchanged since the most recent Prover
  statement → **ANC reached**. Skip to Step 5 (Reporting). Before reporting,
  **write the suggested unit tests** if they don't already exist.

- **`Accept`** but you've changed code since the Prover statement (rare —
  shouldn't happen under this protocol since you don't edit between
  rounds) → treat as `accept_changed`; loop to round k+1 after stabilising.

- **`Challenge`**:
  1. Apply the change(s) that address the challenged sub-claim (edit code).
  2. Add any of the `suggested_tests` that are now relevant (Write/Edit a
     test file).
  3. Increment round counter (k → k+1). If k+1 > 12, jump to Step 4.
  4. Otherwise, emit `[PROVER round k+1]` with updated sub-claims and
     re-invoke the verifier.

- **`Reject`**:
  - Re-think the design. You may restart from a different approach.
  - Increment round counter. If > 12, escalate.
  - Otherwise emit `[PROVER round k+1]` and re-verify.
  - After 3 consecutive `Reject` verdicts, escalate regardless of round
    count.

### Step 4 — Escalate to Opus

On fatigue (k > 12) or 3 consecutive Rejects:

```
Task(
  subagent_type = "pvd-opus-escalator",
  description   = "PVD escalation — fresh design",
  prompt        = <Escalation context — see below>
)
```

#### Escalation context to pass

```
Rounds used: <k>

Full transcript of the failed PVD loop:
<every Prover statement and Verifier verdict, in order>

Code in current state:
<paths + contents>

Outstanding verifier challenges (last 3):
<challenge, with which sub-claim each was targeting>
```

The Opus subagent will either (a) write a redesign itself with Edit/Write, or
(b) recommend a re-scope. Its system prompt (in
`agents/pvd-opus-escalator.md`) handles the choice.

After it returns:
- If (a): the diff is already applied. **Do not re-verify** — Opus's
  authority overrides this round of PVD (otherwise we'd loop forever). Run
  the test suite to confirm nothing broke.
- If (b): pause work, surface the re-scope recommendation to the user, and
  wait for direction.

### Step 5 — Reporting

When the loop terminates (ANC or escalation), summarise to the user in the
final response. Keep it under 6 lines:

```
PVD: <ANC at round k | escalated to Opus at round 12 | rescoped>
Rounds used: k of 12
Challenges addressed: <one-line per round that mattered>
Unit tests added: <list test names>
```

## When NOT to run this skill

- Docs-only changes (`.md`, `.rst`, `.txt`)
- Pure formatting / whitespace / import-sort
- Renames, single-character typo fixes
- Reverts of a recent change
- The user said "quick fix", "no review", "just do it", or similar

If the change is borderline trivial (≤10 LOC, single function, obvious),
state in your response: *"PVD skipped — change is trivial"* and proceed
without the loop.

## Cost note

This protocol multiplies API cost by roughly 2× to 6× depending on rounds
reached. Average expected cost on typical edits is ~1.5–3 rounds, ~5–15
cents per change. Fatigue + Opus escalation can run $1–3. If the user
appears budget-sensitive (e.g. mentioned cost recently), prefer fewer
rounds or skip on borderline cases.

## Mapping to the paper

This skill operationalises the PVD protocol from *Trust but Verify Decoding:
Prover–Verifier Deliberation for Selective Prediction in LLMs* on
code-writing tasks. The verifier's `Accept | Reject | Challenge` verdicts,
the ANC predicate (Accept + No Change), the fatigue limit, and the
retry/escalation pattern all match the protocol used to evaluate GPQA
Diamond. The adaptation to code: sub-claims become claims about
*correctness*, *minimality*, and *test coverage* rather than about
reasoning steps in a multiple-choice answer. The cross-family pairing
(Sonnet ↔ Haiku, escalate to Opus) follows the paper's finding that
capability-asymmetric prover/verifier pairings give the strongest
selection signal.
