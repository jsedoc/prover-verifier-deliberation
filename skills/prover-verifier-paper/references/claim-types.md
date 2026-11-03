# Claim types: how to prove them and how to break them

Read the section for the type you are working on. "Prove" is the prover's checklist; "Break" is
what the verifier tries. Examples are generic; quote the actual paper.

## Contents

- result · comparison · statistical · test · attribution · novelty · generalization · causal ·
  method · theoretical · background
- Overclaim kinds
- Strength ladder

---

## result: "X reaches 81.4% on D"

- **Prove**: anchor the number where it is reported (table row, text, appendix). Confirm the same
  dataset, split, metric, operating point (coverage, threshold, k), model size and condition.
- **Break**: a different number elsewhere (abstract versus table, main text versus appendix); a
  different condition (dev versus test, best seed versus mean); rounding up (81.36 reported as 81.4
  is fine, 81.2 is not); a number that appears only in the text with no table behind it (that
  weakens the anchor but does not by itself contradict it).

## comparison: "X outperforms Y by 9 points", "consistently best"

- **Prove**: anchor both sides; recompute the difference with `diff` (points) or `pct-change` /
  `reduction` (relative). For a named comparator ("the strongest baseline"), find which baseline
  is strongest per dataset and metric. For a quantifier ("all", "every", "consistently", "across"),
  check every cell it covers.
- **Break**: points versus percent confusion (from 20% to 15% is 5 points, a 25% relative
  reduction); the comparator is not the strongest one; one cell where the claim fails (a bold
  number that is not the maximum); differences inside the noise (no variance reported); a
  comparison made on a different subset than the one claimed.

## statistical: "significant (p < 0.01)", "95% CI", "±"

- **Prove**: name the test and its inputs as reported; recompute when counts or summary statistics
  are given: `mcnemar B C` (paired binary outcomes), `fisher` / `two-prop` (independent groups),
  `welch` / `paired-t` (means), `binom`, `wilson` (proportion intervals). Check that the stated
  threshold matches the recomputed p (`--max 0.01`).
- **Break**: the wrong test for the design (an unpaired test on paired data, or the reverse); many
  comparisons without correction; a single run reported as significant with no variance;
  "significant" used without any test; one-sided results presented as two-sided; overlapping
  confidence intervals read as "no difference" (overlap does not imply p > 0.05); n too small for
  the precision implied.

## test: "we test whether H, using Y"

The claim is that test Y establishes (or rules out) hypothesis H. Four sub-claims:

1. **Executed**: Y was run and its results are reported (anchor them).
2. **Valid**: Y separates H from the main alternative explanation. A control should differ from the
   treatment only in the factor under test (same data, steps, compute, tuning budget). A shuffled,
   random, or ablated condition must remove exactly the ingredient H is about.
3. **Adequate**: the sample, number of conditions, seeds and coverage fit the conclusion's scope.
   Three prompts on 50 items cannot establish robustness to prompt wording in general; one dataset
   cannot establish robustness across domains.
4. **Concluded correctly**: the stated inference matches the result and its scope. "No significant
   difference" is not "equivalent" without an equivalence test or a power argument.

- **Break**: a control that changes more than one factor; the test answers a narrower question than
  the one claimed; a null result from an underpowered test read as evidence of absence; the result
  reported only for a favourable subset; details of the test hidden in an appendix that contradict
  the main text (fewer items, different model).

## attribution: "X et al. showed / found / proposed / argued …", "following X, we …"

- **Prove**: quote the cited source. Match the paper's description against the source's own words
  on strength (showed versus suggested), causality (causes versus is associated with), scope (all
  sizes versus some), status (optimal versus default, proposed versus proved, found versus assumed),
  and setting (task, data, population). Abstract-only coverage can support only what the abstract
  says.
- **Break**: the source says something weaker, narrower, or different; the source's own limitations
  disclaim the attributed conclusion; the cited result comes from a different setting than the one
  the paper uses it for; a citation that does not contain the claim at all; the wrong work cited.
- **Uncited attributions** ("prior work assumes …" with no citation) can be supported only by the
  paper's own evidence; otherwise they are unsupported.

## novelty: "first", "novel", "to our knowledge no prior work"

- **Prove**: the paper's own related work and its cited sources must contain no prior instance.
  Without external search the strongest supportable statement is "none of the cited works does X",
  so `supported` is rarely right; prefer `narrowed` or `cannot_prove` (`needs_external`).
- **Break**: the paper itself cites a work that does X (often in related work); a cited source
  describes X under a different name; the novelty depends on a qualifier the claim drops.

## generalization: "generalizes across", "robust to", "in general", "any"

- **Prove**: list the tested conditions (datasets, languages, model families and sizes, domains,
  prompts) and show they span the claimed scope.
- **Break**: tested scope smaller than the claimed scope (one language, one model family); the
  limitations section admits as much; robustness tested on a small or easy subset; an extrapolation
  presented as a finding.

## causal: "driven by", "because", "due to", "explains", "leads to"

- **Prove**: an intervention, ablation, controlled comparison, or analysis that isolates the
  mechanism, with results.
- **Break**: no such analysis exists anywhere in the paper; correlational evidence presented as
  causal; the mechanism asserted in the conclusion but never measured; alternative explanations not
  excluded.

## method: "averaged over five seeds", "n = 500", "we use temperature 0.7"

- **Prove**: the statement plus consistency elsewhere (captions, tables, appendix, experimental
  setup).
- **Break**: internal contradictions (seed count in the text versus "single run" in a caption;
  sample size in the text versus a table's n; a hyperparameter that differs in the appendix).
- **Internal contradictions**: when two statements conflict and nothing in the paper settles which
  is true, both are `contradicted`, each by the other; the inconsistency is the defect and the fix
  is to reconcile them. Cross-table consistency is worth one recomputation: paired counts imply a
  difference in correct answers (first-only minus second-only), which should match the table's
  accuracy gap times the number of items it covers.

## theoretical: "Theorem 1", "we prove", "it follows that"

- **Prove**: the proof is present (body or appendix); assumptions are stated; each step follows;
  the theorem as stated matches what is proved.
- **Break**: a proof sketch presented as a proof; assumptions used but not stated; the stated
  theorem is stronger than the proof (a bound proved for a special case and stated in general).
  When a proof is beyond what you can check, say so (`effective: false`).

## background: "LLMs often hallucinate", "X is widely used"

- **Prove**: a citation that says it (treat as an attribution) or common knowledge in the field
  stated at the same strength.
- **Break**: an uncited strong generalization ("always", "all models"); a citation that does not
  say it. Low priority unless another claim depends on it.

---

## Overclaim kinds

| kind | the claim goes beyond the evidence by… | typical cue |
|---|---|---|
| scope | extending to conditions not tested | "across languages", "in general" |
| quantifier | a universal that some cell contradicts | "all", "every", "consistently", "always" |
| strength | a stronger verb than the evidence allows | "shows", "proves", "demonstrates" |
| causal | turning association into causation | "driven by", "causes", "because" |
| magnitude | a bigger effect than measured, or the wrong comparator | "by 9 points", "more than a third" |
| significance | statistical significance not established at the level stated | "significant", "p < 0.01" |
| novelty | priority that prior work contradicts | "first", "novel" |
| certainty | dropping the hedges the body uses | body "may", abstract "does" |
| attribution | crediting a source with more than it says | "X showed" |
| test-adequacy | a conclusion stronger than its test can support | "robust" from three prompts |

The supported version should be the strongest statement the evidence supports, which usually means
the claim with only the excess removed: "improves selective accuracy by 7.1 points over the
strongest baseline on D1 and 3.2 points on D2", not "improves selective accuracy".

## Strength ladder

From weakest to strongest; a claim may climb no higher than its evidence:
is consistent with → suggests → indicates → provides evidence that → shows → demonstrates →
establishes → proves. Causal language (drives, causes, explains) needs an intervention or isolating
analysis, whatever the verb.
