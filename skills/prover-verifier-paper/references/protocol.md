# Protocol: how this skill instantiates prover-verifier deliberation

Source protocol: Sedoc, Zhang & Foster, *Trust but Verify: Prover-Verifier Deliberation for
Selective LLM Prediction*, arXiv:2605.25133 (2026). That paper evaluates PVD on multiple-choice
questions; this skill applies the same machinery to the claims of a paper. Read this file when you
need the reasoning behind a rule, or when a user asks how the audit relates to the paper.

## Mapping

| PVD (paper) | This skill |
|---|---|
| Question with candidate answers | One claim decomposed from the paper |
| Prover's answer | Prover's position on the claim: supported / narrowed / cannot_prove / contradicted (plus the supported version when narrowed) |
| Statement, 3–5 atomic sub-claims, reasoning | Same, but every sub-claim must be anchored (verbatim quote from the paper or a cited source, or a recomputed check) or marked as reasoning |
| Verifier: Accept / Challenge / Reject | Same, per claim, with the verifier re-opening the anchors and hunting for counter-evidence in the paper and sources |
| Challenge targets the single most suspicious sub-claim | Same (`challenged_subclaim`) |
| Prover may revise its answer | Prover may revise its position; a change counts as an answer change |
| ANC: Accept with no answer revision | Same; the high-confidence verdict |
| Fatigue limit T (12 in the paper) | `--fatigue`, default 6 verifier turns per claim (each turn is heavier than in the QA setting); use 12 for fidelity |
| Challenge-first verifier (PVD†) | `--challenge-first headline,flags` by default: headline claims and every flag (narrowed, cannot_prove, contradicted) need at least one Challenge before Accept |
| Separated histories | Separate agents; the prover sees only the challenged sub-claim and question, never the verifier's reasoning; the verifier sees only the prover's structured output |
| Retry K attempts, then majority vote | Replaced by one adjudicator pass on Reject, fatigue, or an out-of-competence Accept, as in the prover-verifier-code skill's escalator |
| Capability-asymmetric pairing (Sonnet prover, Haiku verifier) | Prover inherits the session model; verifier defaults to Sonnet; adjudicator Opus |
| Effective Verifier assumption (Zhang & Sedoc 2025) | The verifier can declare `effective: false`; such Accepts are never certified and go to the adjudicator |

## What the ledger adds

Frozen models over a noisy channel do not inherit interactive-proof soundness. The ledger removes
the noise it can remove deterministically:

- Every quote (claim, evidence, counter-evidence) must occur verbatim, after normalising case,
  punctuation, accents and LaTeX commands, in `paper.md` or a cached source. Near-misses are refused,
  because one changed digit or a dropped "not" keeps the similarity high.
- Every numeric check is re-run, and the prover must state whether the result supports or undercuts
  the paper's value. A check that contradicts the prover's own description is refused.
- Every search the prover reports is re-run and the true hit count is shown to the verifier.
- Policy is enforced mechanically: challenge-first, fatigue, ANC and the state transitions.

What the ledger cannot check is meaning: whether a quoted passage says what a sub-claim says it says,
whether a test is valid, whether a source's scope matches the paper's use of it. That is the
verifier's job, and it is where the effective-verifier condition matters.

## State machine (per claim)

```
await_prover --(prover submits a valid proof)--> await_verifier
await_verifier --Accept, effective, no change--> done (ANC, confidence high)
await_verifier --Accept, effective, changed----> done (accept_changed, confidence medium)
await_verifier --Accept, not effective---------> await_adjudicator
await_verifier --Reject------------------------> await_adjudicator
await_verifier --Challenge, turns < T----------> await_prover
await_verifier --Challenge, turns = T----------> await_adjudicator (fatigue)
await_adjudicator --(adjudicator label)--------> done (confidence adjudicated)
```

Labels: supported ← supported; overclaim ← narrowed; contradicted ← contradicted; unsupported ←
cannot_prove with `no_evidence`; unverifiable ← cannot_prove with `source_unavailable` or
`needs_external`, or an abandoned claim.

## Interpreting results

- ANC on a flag means the accusation survived a skeptical check without the prover backing off; ANC
  on `supported` means the claim's support survived the same.
- As in the paper, the signal is empirical. When the verifier is out of its depth (dense proofs, a
  field far from its competence), the ANC signal can collapse or even invert; the paper observed
  this on Humanity's Last Exam with weak pairings. Use a stronger verifier for such papers and read
  `effective: false` notes as warnings.
- `unverifiable` is an audit gap. Supplying the missing sources (`refs.py add`) and re-running those
  claims closes it.

## Single-session fallback

Without a subagent tool, one session can play every role in sequence, with each role's turn
submitted through the ledger. The grounding discipline survives (quotes, checks, searches, policy);
role independence and capability asymmetry do not. Say so in the report, as the factual-QA port
does.
