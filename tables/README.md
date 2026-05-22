# Table Reproduction Scripts

These scripts regenerate the LaTeX tables used by `neurips_paper.tex`.

Run from the repository root:

```bash
PYTHONPATH="$PWD" python tables/make_all_tables.py
PYTHONPATH="$PWD" python tables/make_all_tables.py --table 3
PYTHONPATH="$PWD" python tables/make_all_tables.py --table 3 5 8
```

`tables/make_all_tables.py` first refreshes
`tables/generated/summary.json` from the raw files, then renders Tables 3-8.

## Table Index

| Script | LaTeX label | Description |
|---|---|---|
| `table3_gpqa_main.py` | `tab:gpqa-main` | GPQA Diamond main results. |
| `table4_gpqa_domain.py` | `tab:gpqa-domain` | GPQA Diamond ANC by domain. |
| `table5_hle_domain.py` | `tab:hle` | HLE ANC by domain. |
| `table6_verifier.py` | `tab:verifier` | Verifier choice and strictness. |
| `table7_hle_capability.py` | `tab:hle-capability` | HLE gap by model pairing. |
| `table8_sc_pvd.py` | `tab:sc-pvd` | Self-consistency/PVD overlap. |

## Data Sources

RTTR-v1 data live in `data/`. Legacy-format raw files that remain necessary for
the current paper tables live in `data/paper/`.

| File | Used by |
|---|---|
| `data/paper/gpqa_results_single_call_sonnet.json` | Tables 3, 6 |
| `data/paper/gpqa_results_usc.json` | Tables 3, 8 |
| `data/paper/gpqa_results_debate.json` | Table 3 |
| `data/gpqa_results_reflexion.json` | Table 3 |
| `data/paper/gpqa_results_diamond_full.json` | Tables 3, 6 |
| `data/paper/gpqa_results_challenge_sonnet_haiku.json` | Tables 3, 4, 6, 8 |
| `data/paper/gpqa_results_gpt54_direct_baseline.json` | Table 3 |
| `data/paper/gpqa_results_gpt54_sc8.json` | Table 3 |
| `data/paper/gpqa_results_gpt54_xhigh.json` | Tables 3, 4 |
| `data/paper/gpqa_results_gemini_pro_sc8.json` | Table 3 |
| `data/paper/gpqa_results_gemini_pro_flashlite.json` | Tables 3, 4, 6 |
| `data/paper/gpqa_results_gemini_3.1_pro__gpt_5.5_pro_retry.json` | Tables 3, 6 |
| `data/paper/hle_results_gpt_5.5__gemini_3.1_pro.json` | Tables 5, 7 |
| `data/paper/hle_results_sonnet_haiku_full.json` | Table 7 |
| `data/paper/hle_results_opus_challenge_full.json` | Table 7 |

## ANC Definition

Accept + No Change (ANC): the verifier accepts and the prover's answer was not
revised during deliberation.

- Single-attempt legacy files: `final_verdict == "Accept"` and
  `answer_changes == 0`.
- Retry/RTTR files: `outcome == "accept_no_change"`.

## Gap Definition

Gap = high-confidence precision minus accuracy on the non-high-confidence
complement. A positive gap means the confidence signal identifies a more
reliable subset.
