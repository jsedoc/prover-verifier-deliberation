---
name: prover-verifier-factual-qa
description: |
  Apply a chat-native single-session port of Prover–Verifier Deliberation to
  factual questions — open-ended or multiple-choice. Trigger whenever about to
  answer a factual question: historical facts, scientific claims, geography,
  dates, biographical information, legal/regulatory questions, multiple-choice
  knowledge questions, or any claim that can be verified against a structured
  knowledge base. The same session commits to a structured JSON answer with
  atomic sub-claims (prover turn), then switches to a verifier turn that must
  ground each suspect sub-claim against DBPedia and Wikipedia via web_fetch
  before issuing Accept / Reject / Challenge. The loop runs up to 12 rounds
  (fatigue limit). On fatigue or Reject, a final independent-verification pass
  resolves the question or abstains. One PVD loop per question, then a
  definitive answer or abstain. Skip for: code questions, bash commands,
  opinion/creative writing, pure math, and explicitly subjective questions.
---

# Factual-QA: Prover–Verifier Deliberation (chat-native port)

## What this is, and what it is NOT

This skill is a **single-session port** of the PVD protocol from the paper, adapted
for environments without a `Task` / subagent primitive (e.g. Claude web/app, as
opposed to Claude Code). The original protocol's empirical claim — a +32pp
Accept/Reject gap on GPQA Diamond at 55–63% coverage — depends on
**capability-asymmetric** verification: a weaker grounded verifier (Haiku + KB)
checking a stronger prover (Sonnet). That asymmetry is the source of the
selection signal.

**A single-session port cannot reproduce that asymmetry.** Here the same model
plays prover and verifier in alternating turns. Do not claim paper-equivalent
accuracy gains from this port. What it preserves is the **commitment
structure** (atomic falsifiable sub-claims committed before verification) and
the **grounding discipline** (every verifier verdict must be backed by a
real DBPedia or Wikipedia fetch). That is still strictly more rigorous than
naive answering — it just is not the paper's protocol.

For the faithful multi-model version, use the artifact + Anthropic API
variant, not this one.

## Protocol

### Step 1 — Prover turn (round k=0)

Open a section in the response titled `## Prover turn (k=0)` and emit exactly
this JSON block:

```json
{
  "answer": "<letter A/B/C/D for multiple-choice; concise answer phrase for open-ended>",
  "statement": "one sentence explaining your answer",
  "subclaims": [
    "atomic, independently checkable claim 1",
    "atomic, independently checkable claim 2",
    "..."
  ],
  "reasoning": "brief step-by-step explanation (2–3 sentences)"
}
```

Rules for the prover turn:
- 3–5 sub-claims. Each must be **atomic** (one fact per claim) and **falsifiable** (a knowledge base can confirm or deny it).
- Never contradict a previous sub-claim without explicitly acknowledging the correction.
- The `answer` field may be revised in later rounds if a challenge exposes a mistake; track revisions so ANC can be evaluated.
- Keep it concise: statement (1 sentence), sub-claims (3–5 items), reasoning (2–3 sentences).

### Step 2 — Verifier turn (same session, different discipline)

Open a section titled `## Verifier turn (k=<round>)`. In this turn, set aside
the prover reasoning entirely — do not extend or defend it. Treat the prover
JSON as input from a separate party whose claims must survive grounded check.

**Default to Challenge.** Factual questions hard enough to warrant this
protocol almost always have at least one sub-claim worth probing.

**Mandatory grounding before verdict.** Before emitting the JSON verdict, at
least one `web_fetch` to an allowed knowledge-base URL must occur, unless the
verdict is `Challenge` based on a logical/internal contradiction visible
without external check.

#### Allowed sources

DBPedia (primary — use for stable factual claims):

1. Entity lookup:
   ```
   https://lookup.dbpedia.org/api/search?QueryString={term}&MaxHits=3&format=json
   ```
2. Entity properties:
   ```
   https://dbpedia.org/data/{EntityName}.json
   ```
   (EntityName uses underscores, e.g. `Albert_Einstein`.)

Wikipedia (secondary — use for recent or current-events claims):

```
https://en.wikipedia.org/api/rest_v1/page/summary/{page_title}
```

**Do NOT** fetch other URLs and do NOT use `web_search` during the verifier
turn. The grounding restriction is what makes the verifier a verifier and not
just a second pass of the prover. If a sub-claim cannot be checked via DBPedia
or Wikipedia (pure mathematical reasoning, abstract logic, very local or very
recent facts), evaluate it on reasoning alone and record `source_checked:
null` with a note in `reasoning`.

#### Verdict format

Emit exactly one fenced JSON block:

```json
{
  "verdict": "Accept" | "Reject" | "Challenge",
  "reasoning": "2–3 sentence explanation of the decision",
  "challenge": "specific question targeting one sub-claim, or null",
  "challenged_claim": "exact text of the sub-claim being challenged, or null",
  "source_checked": "DBPedia/Wikipedia URL fetched, or null if none"
}
```

#### Calibration

A verifier that has issued zero Challenges across a session is miscalibrated.
A verifier that issues Challenges without any KB fetch is also miscalibrated.
The discipline is: probe, ground, verdict — in that order.

### Step 3 — Handle the verdict

**`Accept` AND the answer is substantively unchanged since round 0 (or since
the most recent revision)** → **ANC reached**. Report the final answer with the
number of rounds used and the list of sources checked. Stop.

**`Accept` with a prior revision** → treat as `accept_changed`. The answer is
still final. Report it as such.

**`Challenge`**:
1. Open `## Prover turn (k=<round+1>)` and emit a revised JSON block:

   ```json
   {
     "answer": "<letter for MC, or answer phrase for open-ended — may be revised>",
     "statement": "<updated or unchanged statement>",
     "subclaims": ["<updated list>"],
     "reasoning": "<deeper justification for the challenged claim specifically>"
   }
   ```

2. Increment the round counter. **If the new round number would exceed 12, jump to Step 4.**
3. Otherwise loop to Step 2 with the new round number.

**`Reject`** → Jump to Step 4 immediately. Do not attempt another round.

### Step 4 — Fallback resolution (replaces Opus escalator)

Triggered on fatigue (k > 12) or any `Reject`. Open a section titled
`## Fallback resolution`. In this turn:

1. **Do not anchor** on the prover's most recent answer. Re-derive
   independently: identify the 2–3 most critical factual claims needed to
   answer the question. Note: this independence is by discipline only — it is
   the same model as the prover. Single-session limitation. Acknowledge that
   in the output.
2. Look each critical claim up via DBPedia (primary) and Wikipedia (secondary).
   `web_fetch` only those endpoints. No `web_search`.
3. Pick one of two paths.

#### Path (a) — Definitive answer

```json
{
  "path": "answer",
  "answer": "<letter A/B/C/D for MC; concise answer phrase for open-ended>",
  "statement": "one sentence explaining the correct answer",
  "subclaims_verified": [
    {"claim": "...", "source": "<URL or 'reasoning'>", "result": "confirmed | refuted | unverifiable"}
  ],
  "why_prover_failed": "one sentence on where the loop got stuck",
  "single_session_caveat": "Same-model fallback — independence is by discipline only, not by capability separation."
}
```

#### Path (b) — Abstain

```json
{
  "path": "abstain",
  "reason": "one sentence on why the question is unanswerable from available sources",
  "what_would_help": "what additional context or source would resolve the question"
}
```

After the fallback returns, surface the result to the user. Do not loop again.

### Step 5 — Report

```
Answer: <answer — letter for MC, phrase for open-ended>
Terminal state: <ANC at round k | accept_changed at round k | fallback (answer) | fallback (abstain)>
Rounds used: k of 12
Source checks: <list of DBPedia/Wikipedia URLs fetched across all turns>
Single-session caveat: This was a chat-native port of PVD. Same-model verification — no capability asymmetry, no paper-equivalent selection guarantee.
```

## Examples

### Example A — multiple-choice, ANC at round 0

Question: *Which element has atomic number 79? A) Silver B) Gold C) Platinum D) Copper*

`## Prover turn (k=0)`:
```json
{
  "answer": "B",
  "statement": "Atomic number 79 corresponds to gold (Au).",
  "subclaims": [
    "Atomic number is the count of protons in the nucleus of an element.",
    "Gold has the chemical symbol Au and atomic number 79.",
    "Gold is a period-6 transition metal in group 11."
  ],
  "reasoning": "The periodic table places Au at Z=79; this is a standard inorganic chemistry fact."
}
```

`## Verifier turn (k=0)`: `web_fetch` `https://dbpedia.org/data/Gold.json` — confirms atomic number 79. Verdict:
```json
{
  "verdict": "Accept",
  "reasoning": "DBPedia property dbo:atomicNumber on Gold returns 79. The sub-claims are atomic, the central one is confirmed, and the remaining sub-claims are stable textbook facts.",
  "challenge": null,
  "challenged_claim": null,
  "source_checked": "https://dbpedia.org/data/Gold.json"
}
```

ANC at round 0. Answer: B.

### Example B — open-ended, ANC at round 1

Question: *What is the capital of Suriname?*

`## Prover turn (k=0)`:
```json
{
  "answer": "Paramaribo",
  "statement": "Paramaribo is the capital and largest city of Suriname.",
  "subclaims": [
    "Suriname is a country on the northeastern coast of South America.",
    "Paramaribo serves as Suriname's capital and seat of government.",
    "Paramaribo is located on the Suriname River, near the Atlantic coast."
  ],
  "reasoning": "Paramaribo is the administrative, political, and cultural centre of Suriname and is listed as its capital in standard geographic references."
}
```

`## Verifier turn (k=0)`: `web_fetch` `https://dbpedia.org/data/Paramaribo.json`. Challenge the third sub-claim — DBPedia gives a specific distance from the coast and the sub-claim is vague. Verdict:
```json
{
  "verdict": "Challenge",
  "reasoning": "The first two sub-claims are confirmed by DBPedia. The third is vague ('near the Atlantic coast') — DBPedia places Paramaribo roughly 15 km inland on the Suriname River. Probing precision.",
  "challenge": "How far inland is Paramaribo, and is 'near the Atlantic coast' the right characterisation?",
  "challenged_claim": "Paramaribo is located on the Suriname River, near the Atlantic coast.",
  "source_checked": "https://dbpedia.org/data/Paramaribo.json"
}
```

`## Prover turn (k=1)`: revise the third sub-claim to "Paramaribo is on the Suriname River approximately 15 km from its Atlantic mouth." Answer unchanged.

`## Verifier turn (k=1)` → `Accept`. ANC at round 1. Answer: Paramaribo.

## Mapping to the paper

| Paper term | Original CC skill | This chat-native port |
|---|---|---|
| Prover | Sonnet, main session, Task-orchestrated | Same session, `## Prover turn` marker |
| Verifier | Haiku subagent via Task | Same session, `## Verifier turn` marker, discipline-enforced |
| Verifier grounding | DBPedia + Wikipedia (via WebFetch) | DBPedia + Wikipedia (via web_fetch) |
| ANC predicate | Accept + answer substantively unchanged | Same |
| Fatigue limit | 12 rounds | 12 rounds |
| Escalation | Opus subagent via Task | Same-session fallback resolution turn |
| Capability asymmetry | Haiku ≠ Sonnet ≠ Opus | **Lost** — same model throughout |
| Selection signal (paper's +32pp claim) | Present | **Not preserved** |

## What this port preserves vs. drops

**Preserved:**
- Commitment-before-verification structure
- Atomic-falsifiable sub-claim discipline
- Mandatory KB grounding for verdicts (DBPedia primary, Wikipedia secondary, no other sources)
- Default-to-Challenge bias
- ANC as terminal success condition
- Fatigue limit of 12 rounds, with no retries

**Dropped or degraded:**
- Capability-asymmetric verification (the paper's core selection mechanism)
- Independent escalator (now same-model fallback by discipline only)
- Paper-equivalent empirical guarantees — do not cite the +32pp Accept/Reject
  gap when using this port
