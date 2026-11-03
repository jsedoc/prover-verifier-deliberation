# prover-verifier-paper

A Claude Code skill that audits the claims of a research paper with prover-verifier deliberation
(PVD; Sedoc, Zhang & Foster, arXiv:2605.25133).

1. **Decompose**: a decomposer agent turns the paper into atomic claims, each quoted verbatim with
   its line: results, comparisons, statistics, "X et al. showed" attributions, "we test H using Y"
   claims, novelty, generalization, causal and method claims.
2. **Prove**: a prover agent tries to show each claim true from the paper's results, its cited
   sources and recomputed numbers. Claims it cannot prove are flagged `unsupported`; claims it can
   prove only in a weaker form are flagged `overclaim`, with that weaker form; claims the evidence
   refutes are `contradicted`.
3. **Deliberate**: a verifier agent that sees only the prover's structured output re-opens the
   evidence and challenges the weakest sub-claim until it accepts or rejects. Accept + No Change is
   the high-confidence outcome; Reject and fatigue go to an adjudicator.

A deterministic ledger referees the protocol: quotes must occur verbatim in the paper or a cached
source (near-misses are refused, so a changed digit or a dropped "not" cannot slip through), numeric
checks and searches are re-run, and challenge-first, fatigue and ANC are enforced mechanically.

## Install

```bash
./install.sh             # links the skill and its 4 agents into ~/.claude (user scope)
./install.sh --project   # or into ./.claude of the current repo
pip install pymupdf      # optional: better PDF text, figure rendering, hidden-text scan
```

Inside the `prover-verifier-deliberation` repo, copy this folder to `skills/prover-verifier-paper/`
and run `./skills/install.sh prover-verifier-paper --global`. Start a new session; `/agents` should
list `pvd-paper-decomposer`, `pvd-paper-prover`, `pvd-paper-verifier` and `pvd-paper-adjudicator`.

## Use

```
/prover-verifier-paper 2605.25133
/prover-verifier-paper ~/papers/draft/main.tex headline claims only
/prover-verifier-paper paper.pdf --fatigue 12
```

or just ask: "check whether the claims in this paper hold up". The result is
`pvd-audit/<slug>/report.md`: severity-ranked flags, each with the quote, the finding, the
evidence, the strongest supported version, a suggested fix and the deliberation path, followed by a
table of every claim.

## Layout

```
SKILL.md                     orchestration (what the main session does)
agents/                      decomposer, prover, verifier, adjudicator (system prompts + models)
references/claim-types.md    per-type proving and breaking checklists; overclaim taxonomy
references/protocol.md       mapping to the PVD paper; state machine; how to read results
scripts/ingest.py            arXiv / PDF / LaTeX / markdown → line-anchored paper.md + references
scripts/refs.py              fetch or register cited sources (arXiv, ACL, OpenReview, Semantic Scholar)
scripts/ledger.py            the protocol referee and packet writer
scripts/check_numbers.py     arithmetic and statistical re-checks (McNemar, Fisher, Welch, Wilson, …)
scripts/render_page.py       render a PDF page so agents can look at a figure
scripts/render_report.py     report.md + report.json
scripts/tests/               python3 -m unittest discover -s scripts/tests
evals/audit-trap-paper/      a fictional paper with 12 planted defects and an answer key
```

The scripts use only the Python standard library (PyMuPDF optional). The eval case follows the
`claude plugin eval` layout: `claude plugin eval . --scaffold --allow-tools Write,Edit,Bash,Agent`.
