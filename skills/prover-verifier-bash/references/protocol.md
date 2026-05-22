# PVD Protocol — Theoretical Notes

## Lineage

This skill operationalizes the Prover-Verifier-Decoder framing from the **Challenge Decoding** line of work (Sedoc, Zhang, Foster, et al.). The core empirical finding motivating runtime adoption: on GPQA Diamond, the joint protocol opens a **+32pp Accept/Reject gap** at **55–63% coverage** — i.e., conditional on the protocol terminating in Accept, accuracy is materially higher than the unconditional baseline, and the protocol abstains often enough to be useful but not so often as to be vacuous.

The protocol relates to but is distinct from prior work in this area:

- **AI Safety via Debate** (Irving et al.): cooperative-adversarial debate aimed at a human judge. PVD is single-round and aimed at machine-decidable Accept / Challenge verdicts.
- **Hammond & Adam-Day (2024)**: zero-knowledge-style proof protocols. PVD borrows the prover/verifier vocabulary but does not claim soundness in the cryptographic sense — challenges are heuristic.
- **Multi-Agent Debate, Best-of-N, Reflexion**: baselines in the empirical comparison. PVD's distinctive feature is the *commitment structure* — the prover commits to a structured claim before the verifier's adversarial pass.

## Why bash is a natural application

The selective-prediction setup pays off when:

1. **Actions have asymmetric cost.** A wrong answer on a multiple-choice benchmark costs one point. A wrong `rm -rf` can cost a working directory or a production deployment. Bash sits at the high-asymmetry end.
2. **The prover has private structure to commit to.** When Claude generates a bash command, it has implicit beliefs about the working directory, the file system state, and the intended outcome. Making these explicit creates surfaces the verifier can challenge.
3. **An independent verifier can be cheaply instantiated.** A second LLM call (or a freshly-framed self-verification turn) costs hundreds of tokens. The asymmetry justifies it.

## Mapping paper terms to this skill

| Paper term                | Skill term                                              |
|---------------------------|---------------------------------------------------------|
| Prover                    | Claude generating the proposal block                    |
| Verifier                  | The challenger pass (API or in-context)                 |
| Coverage                  | Fraction of bash invocations that reach ANC             |
| Accept (terminal)         | ANC (Accept, No Challenge)                              |
| Reject (terminal)         | Escalation to human                                     |
| Accept/Reject gap         | Counterfactual: error rate of ANC-passed vs. ungated    |

## Calibration knobs

If the skill produces too many escalations:
- The verifier checklist is over-tight, or
- The prover is being lazy in the proposal (under-specifying `EXPECTED` or `BLAST_RADIUS`).

If the skill produces too few challenges:
- The verifier is sycophantic (the most common in-context failure mode); switch to the API-based verifier for high-stakes commands, or
- The fast-path filter is admitting commands that should be gated.

Track these over a session as a coverage / quality tradeoff, the same way the paper plots them.

## Open questions

- **Verifier independence.** In-context self-verification has known correlation with the prover. An independent API call helps but is still the same model family. A meaningful next step is a cross-family verifier.
- **Retry dynamics.** The current policy (max 2 retries, materially-different revisions) is heuristic. A principled version would track the verifier's posterior across retries and stop when it stops moving.
- **Skill scope.** Bash is the most natural runtime target. Equivalent gates for file edits (`str_replace`, `create_file`) and network actions would extend the protocol; the same proposal/verdict structure should generalize.
