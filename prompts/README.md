# Prompts

Every prompt used by `rttr/*.py` lives here as a separate `.txt` file.
Each file starts with a header block documenting:

- Which script uses it
- Which protocol (PVD variant, Debate, Reflexion)
- The Python `.format()` variables it expects (or `none` for static prompts)
- Paper reference section
- Last change date

Lines beginning with `#` at the top of a file are stripped on load
(comments are not part of the prompt sent to the model). The first
non-`#` line is the start of the prompt body.

## Files

| File                              | Used by              | Description                                    |
|-----------------------------------|----------------------|------------------------------------------------|
| `pvd_prover_system.txt`           | `rttr/pvd.py`        | Prover system prompt                           |
| `pvd_verifier_system.txt`         | `rttr/pvd.py`        | Verifier system prompt (standard)              |
| `pvd_verifier_system_min1.txt`    | `rttr/pvd.py`        | Verifier system prompt (forced ≥1 challenge)   |
| `pvd_verifier_first_user.txt`     | `rttr/pvd.py`        | First user msg to verifier (prover's statement)|
| `pvd_prover_challenge_response.txt` | `rttr/pvd.py`      | User msg to prover after a challenge           |
| `pvd_verifier_next_round.txt`     | `rttr/pvd.py`        | User msg to verifier after prover responds     |
| `pvd_retry_prior_context.txt`     | `rttr/pvd.py`        | Prefix injected before question on retry attempts (retry variant only) |
| `debate_system.txt`               | `rttr/debate.py`     | System prompt for all debate agents            |
| `debate_agent_initial.txt`        | `rttr/debate.py`     | Round-0 prompt (each agent independent)        |
| `debate_agent_update.txt`         | `rttr/debate.py`     | Rounds 1+ prompt (after seeing peers)          |
| `reflexion_attempt_system.txt`    | `rttr/reflexion.py`  | Actor system prompt (attempt and recheck)      |
| `reflexion_attempt_user.txt`      | `rttr/reflexion.py`  | Actor user prompt (initial attempt + recheck)  |
| `reflexion_reflect_system.txt`    | `rttr/reflexion.py`  | Critic system prompt (reflection generation)   |
| `reflexion_reflect_user.txt`      | `rttr/reflexion.py`  | Critic user prompt (asks for reflection)       |
| `reflexion_retry_user.txt`        | `rttr/reflexion.py`  | Actor user prompt (retry with reflection)      |
| `usc_selector_system.txt`         | `rttr/usc.py`        | Selector system prompt                         |
| `usc_selector_user.txt`           | `rttr/usc.py`        | Selector user prompt (question + k candidates) |

## Editing prompts

Changing a prompt invalidates prior results. After any semantic edit:

1. Update the `Last changed` date in the file header.
2. Bump the `schema_version` in `rttr/common.py` (e.g. `rttr-v1` → `rttr-v2`).
3. Re-run any affected configs in `configs/`.
