---
name: prover-verifier-bash
description: Gate bash command execution through a Prover-Verifier-Decoder (PVD) protocol with retry and human escalation. Use this whenever Claude is about to run a non-trivial bash command — anything that writes to disk, mutates state, installs packages, hits the network, runs git operations, or could be destructive. Claude generates a structured proposal (command, context, intent, expected outcome, blast radius), an independent Haiku verifier subagent challenges it, and execution only proceeds when Accept-with-No-Challenge (ANC) is reached. On challenge, retry with refined justification up to twice; if ANC is still not reached, escalate to the human with the full transcript. Trigger this for `pip install`, `npm install`, `git push`, `rm`, `mv`, `cp`, `curl`, `wget`, redirects (`>`, `>>`), `chmod`, `sudo`, scripts touching `/mnt/user-data/` outside the sandbox, and any command whose effect Claude cannot fully predict. Skip only for pure-read inspection like `ls`, `pwd`, `cat`, `head`, `grep`, `which`, `echo $VAR`.
---

# Prover-Verifier-Bash: Prover-Verifier-Decoder Gating for Bash Execution

## Why this exists

This skill operationalizes the **Challenge Decoding** protocol (Sedoc et al., +32pp Accept/Reject gap on GPQA Diamond at 55–63% coverage) as a runtime gate on bash execution. The core claim from that line of work: when a prover commits to a structured claim and an independent verifier is given license to challenge, the joint protocol selectively predicts — answering confidently when the claim is well-grounded and abstaining when it is not. Bash commands are exactly the kind of high-leverage action where selective prediction matters: a correct command is cheap, a wrong one can be expensive or irreversible.

The terminal state of the protocol is **ANC (Accept, No Challenge)**: the verifier examined the prover's proposal and declined to challenge it. Only ANC permits execution. Anything else either triggers a retry or escalates to the human.

> **Prerequisite.** This skill invokes the `pvd-bash-verifier` subagent (Haiku, read-only). It ships alongside this SKILL.md under `agents/`. If `/agents` does not list it, the install was incomplete — run `./skills/install.sh prover-verifier-bash` from the source repo before continuing.

## When to run the full protocol (vs. the fast path)

**Fast path — skip the protocol, just run the command:**

Pure-read inspection with no side effects: `ls`, `pwd`, `cat`, `head`, `tail`, `wc`, `grep`, `find`, `which`, `whoami`, `date`, `echo`, `printenv`, `git status`, `git log`, `git diff`.

Note: `python -c` is **not** on the fast path even for "pure computation" — Python code can import modules, open files, or spawn subprocesses.

**Full protocol — required:**

Anything that mutates state. File writes (`>`, `>>`, `tee`), installs (`pip`, `npm`, `apt`, `brew`), git mutations (`commit`, `push`, `merge`, `rebase`, `reset`, `checkout -b` is fine but `checkout file` rewrites local state), removal/move (`rm`, `mv`, `rmdir`), permission changes (`chmod`, `chown`), network egress (`curl`, `wget`, anything posting to an API), redirects into existing files, anything with `sudo`, anything piped into `sh` or `bash`, anything operating on paths Claude did not just create itself in this session.

**When in doubt, run the protocol.** The cost of the protocol is a few hundred tokens; the cost of a wrong `rm` is unbounded.

## The protocol

### Step 1 — Prover proposal

Before invoking `bash_tool`, emit a proposal block. Use exactly this format:

```
<pvd-proposal attempt="1">
COMMAND:        <the exact command to run, single line if possible>
CONTEXT:        <what just happened; current working directory; what files exist that matter>
INTENT:         <the goal this command serves, in one sentence>
EXPECTED:       <what changes if the command succeeds: files created/modified, exit code, stdout shape>
BLAST_RADIUS:   <what can this affect if it goes wrong; is it reversible; what's the worst case>
ALTERNATIVES:   <one safer alternative considered and why it was rejected, or "none considered">
</pvd-proposal>
```

The proposal must be honest. If you don't know what `EXPECTED` looks like, say so — that itself is a signal the verifier should challenge.

### Step 2 — Invoke the verifier subagent

Use the `Task` tool with `subagent_type="pvd-bash-verifier"`. The subagent's system prompt handles the checklist and verdict logic; your job here is to package the round context.

```
Task(
  subagent_type = "pvd-bash-verifier",
  description   = "PVD-Bash verify attempt <k>",
  prompt        = <Round context — see below>
)
```

#### Round context to pass

```
Attempt: <k>

Proposal:
<the full <pvd-proposal attempt="k"> block>

Prior attempts (for k > 1):
<concise list of (attempt, verdict, category, reason) tuples>
```

Do not repeat the verifier's checklist or instructions in this prompt — those live in the subagent's own system prompt. Just pass the round context.

### Step 3 — Handle the verdict

The verifier returns a single fenced JSON block:

```json
{
  "verdict": "ACCEPT" | "CHALLENGE" | "ABSTAIN",
  "category": "<category name, or null>",
  "reason": "<one sentence, or null if ACCEPT>",
  "suggested_revision": "<concrete change to make, or null if ACCEPT>"
}
```

Parse it. Then:

- **`ACCEPT`** AND the command is unchanged since the proposal → **ANC reached**. Copy the exact string from `COMMAND:` in the accepted proposal into the bash invocation — do not re-derive or rephrase it. Run it. Stop.

- **`CHALLENGE`**:
  1. Address the `suggested_revision` — revise the proposal and/or the command.
  2. Increment attempt counter (k → k+1). **If k+1 > 3, jump to Step 4 immediately. Do not attempt a 4th round under any circumstances.**
  3. Otherwise emit `<pvd-proposal attempt="k+1">` with the revision and re-invoke the verifier.

- **`ABSTAIN`**: Escalate immediately to the human. Do not retry.

Retries must materially change the proposal. A retry that re-states the original intent without addressing the challenge category is itself grounds for escalation — flag it and stop.

> **Hard limit.** The attempt counter is a hard stop, not a soft guideline. If the 3rd attempt receives CHALLENGE, go directly to Step 4 — do not reason about whether "one more try" would work.

### Step 4 — Escalation

When ANC is not reached after 3 attempts (or on any ABSTAIN), present the full transcript to the human and ask for an explicit decision. Use this format:

```
PVD escalation — ANC not reached.

Command:        <the proposed command>
Attempts:       <number>
Final verdict:  <CHALLENGE: category | ABSTAIN>

Transcript:
[attempt 1 proposal → verdict]
[attempt 2 proposal → verdict]
[attempt 3 proposal → verdict]

Options:
  1. Approve and run once.
  2. Approve and skip PVD for the following exact command pattern (specify): ___________
  3. Reject — propose a different approach.
```

Wait for the human's explicit selection. Do not run the command on silence.

## Examples

### Example 1 — clean ANC on attempt 1

The user asked to install pandas.

```
<pvd-proposal attempt="1">
COMMAND:        pip install pandas --break-system-packages
CONTEXT:        Fresh container, no venv. /home/claude is writable. About to load a CSV in the next step.
INTENT:         Install pandas so the upcoming CSV analysis script can import it.
EXPECTED:       Installs pandas + numpy + dateutil + pytz; exit 0; prints "Successfully installed ..." lines.
BLAST_RADIUS:   Modifies system Python site-packages. Reversible via pip uninstall. No network impact beyond PyPI.
ALTERNATIVES:   Considered a venv — rejected because the container resets between tasks anyway.
</pvd-proposal>
```

```json
{ "verdict": "ACCEPT", "category": null, "reason": null, "suggested_revision": null }
```

ANC reached → run `pip install pandas --break-system-packages` (exact string from COMMAND).

### Example 2 — challenge → revised → ANC

```
<pvd-proposal attempt="1">
COMMAND:        rm -rf build/
INTENT:         Clean stale build artifacts before rebuilding.
EXPECTED:       Removes build directory.
BLAST_RADIUS:   Low; build is regeneratable.
ALTERNATIVES:   none considered
</pvd-proposal>
```

```json
{
  "verdict": "CHALLENGE",
  "category": "path-ambiguity",
  "reason": "\"build/\" is a relative path; cwd was not stated, and a top-level build/ holds published artifacts.",
  "suggested_revision": "State cwd explicitly and use an absolute path, e.g. rm -rf /home/claude/project-x/build/"
}
```

Retry:

```
<pvd-proposal attempt="2">
COMMAND:        rm -rf /home/claude/project-x/build/
CONTEXT:        cwd is /home/claude. Verified /home/claude/project-x/build/ exists and contains stale .o files from this session. Top-level /home/claude/build/ is the published artifact directory and is untouched.
INTENT:         Clean project-x's local build directory before rebuilding.
EXPECTED:       Removes /home/claude/project-x/build/ and its contents; exit 0; no output.
BLAST_RADIUS:   Bounded to /home/claude/project-x/build/. Regeneratable in the next make step.
ALTERNATIVES:   Considered `make clean` — rejected because the Makefile is currently broken.
</pvd-proposal>
```

```json
{ "verdict": "ACCEPT", "category": null, "reason": null, "suggested_revision": null }
```

### Example 3 — escalation

Three CHALLENGE verdicts in a row on a `git push --force` to `main`. Stop, present the transcript, ask the human.

## Bias correction notes

- **Verifier sycophancy.** The Haiku verifier is deliberately less capable than the Sonnet prover — the capability gap is the source of the selection signal. If you notice the verifier ACCEPTing every proposal without challenge, something is wrong; surface it.
- **Prover capitulation.** On retry, do not just reword the proposal without changing the command. The revision must materially address the `suggested_revision`.
- **Abstain inflation.** ABSTAIN exists for genuine uncertainty about command behavior, not for user-intent ambiguity. If the intent is unclear, ask the user directly before starting the protocol.

## Reference

For the protocol's theoretical motivation, see `references/protocol.md`.
