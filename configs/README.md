# Configs

YAML configs for each RTTR run. Run any config with:

```
python -m rttr.run --config configs/<name>.yaml
```

Use `--n N` to override `dataset.n` (handy for pilots):

```
python -m rttr.run --config configs/pvd_standard.yaml --n 10 --output-suffix _pilot
```

| File                | Protocol  | Variant                       | Approx cost (198 q) |
|---------------------|-----------|-------------------------------|--------------------:|
| `pvd_standard.yaml` | PVD       | no min-challenge, no retry    | ~$10                |
| `pvd_min1.yaml`     | PVD       | min-1 challenge, no retry     | ~$18                |
| `pvd_self.yaml`     | PVD       | self-play (Sonnet/Sonnet), min-1 | ~$26              |
| `pvd_retry.yaml`    | PVD       | max-5 attempts                | ~$26                |
| `debate.yaml`       | Debate    | 3 agents × 2 rounds           | ~$25                |
| `reflexion.yaml`    | Reflexion | max 5 trials                  |  ~$4                |

All runs:
- GPQA Diamond, n=198, seed=42
- No extended thinking (`thinking_budget_tokens: 0`)
- Schema: `rttr-v1` (summarized in the repository README)

After a run finishes, the result lives in `data/gpqa_results_<run_key>.json`.

## Schema

Every config must have these top-level keys:
`run_key`, `dataset`, `protocol`, `logging`, `compute`.

Plus a protocol-specific block named after the `protocol` field
(`pvd`, `debate`, or `reflexion`).
