# Claude Code skills

This directory holds Claude Code [skills](https://docs.claude.com/en/docs/claude-code/skills)
and the [subagents](https://docs.claude.com/en/docs/claude-code/sub-agents) they
depend on, authored as part of this project.

## Available skills

| Skill | What it does | When it triggers |
|---|---|---|
| [`prover-verifier-code`](prover-verifier-code/SKILL.md) | Runs the paper's Prover–Verifier Deliberation protocol on code you're about to write. The prover (main session, Sonnet 4.6) spawns a Haiku 4.5 verifier subagent for up to 12 rounds; on fatigue it escalates to Opus 4.7. | Whenever Claude is about to write or substantially modify code (≥10 LOC, non-trivial). Skips docs, renames, formatting. |
| [`prover-verifier-bash`](prover-verifier-bash/SKILL.md) | Gates state-mutating shell commands through the PVD protocol. The prover proposes a structured JSON verdict (ACCEPT / CHALLENGE / ABSTAIN); a capability-asymmetric Haiku verifier subagent evaluates it against a security checklist before execution proceeds. | Before any bash command that creates, deletes, overwrites, or otherwise mutates system state. Read-only commands are passed through. |

## Layout of a skill in this repo

Each skill lives in its own subdirectory and bundles the SKILL.md *plus* any
subagents it depends on:

```
skills/prover-verifier-code/
├── SKILL.md              # the skill body itself
└── agents/               # named subagents the SKILL.md invokes by type
    ├── pvd-verifier.md
    └── pvd-opus-escalator.md

skills/prover-verifier-bash/
├── SKILL.md
├── references/
│   └── protocol.md       # security-checklist the verifier applies
└── agents/
    └── pvd-bash-verifier.md
```

Claude Code looks for skills under `~/.claude/skills/` (or `.claude/skills/`)
and subagents under `~/.claude/agents/` (or `.claude/agents/`), so installing a
skill that uses subagents means symlinking *both*. The `install.sh` script in
this directory does that in one step.

## Installing

Two scopes:

| Scope | Activates for | Skill goes to | Agents go to |
|---|---|---|---|
| User (global) | every Claude Code session on this machine | `~/.claude/skills/<name>/` | `~/.claude/agents/*.md` |
| Project | only this repository | `.claude/skills/<name>/` | `.claude/agents/*.md` |

The tracked source of truth stays in `skills/`. We symlink it into the active
location so edits in git propagate automatically.

### Global install (recommended)

Install both skills together:

```bash
./skills/install.sh prover-verifier-code --global
./skills/install.sh prover-verifier-bash --global
```

Or install individually:

```bash
./skills/install.sh prover-verifier-code --global   # code-writing gate
./skills/install.sh prover-verifier-bash --global   # bash-execution gate
```

Or by hand (example for `prover-verifier-code`):

```bash
mkdir -p ~/.claude/skills ~/.claude/agents
ln -snf "$(pwd)/skills/prover-verifier-code" ~/.claude/skills/prover-verifier-code
for a in skills/prover-verifier-code/agents/*.md; do
  ln -snf "$(pwd)/$a" ~/.claude/agents/"$(basename "$a")"
done
```

### Per-project install

```bash
./skills/install.sh prover-verifier-code --project
./skills/install.sh prover-verifier-bash --project
```

### Verify

```bash
ls -l ~/.claude/skills/prover-verifier-code/SKILL.md
ls -l ~/.claude/skills/prover-verifier-bash/SKILL.md
ls -l ~/.claude/agents/pvd-*.md
```

In a fresh Claude Code session, the skills should appear in the auto-loaded
list. Confirm with `/skills`; the subagents show up under `/agents`.

### Uninstall

```bash
./skills/install.sh prover-verifier-code --uninstall          # global
./skills/install.sh prover-verifier-bash --uninstall          # global
./skills/install.sh prover-verifier-code --uninstall --project
./skills/install.sh prover-verifier-bash --uninstall --project
```

(Source under `skills/` stays in git either way.)

## Authoring a new skill

1. `mkdir -p skills/<your-skill-name>` (plus `agents/` inside if it uses
   subagents).
2. Write `skills/<your-skill-name>/SKILL.md` with YAML frontmatter:

   ```yaml
   ---
   name: your-skill-name
   description: |
     One-paragraph trigger description. Claude reads this to decide when to
     invoke the skill. Be specific about what it does AND when it should fire
     (and when it shouldn't).
   ---

   # Skill body
   …instructions for Claude when the skill is active…
   ```

3. Define any subagents under `skills/<your-skill-name>/agents/<agent-name>.md`,
   again with YAML frontmatter:

   ```yaml
   ---
   name: agent-name
   description: When the main agent should delegate to this subagent.
   model: haiku                       # or sonnet, opus
   tools: Read, Glob, Grep            # omit Write/Edit for read-only roles
   ---

   # Subagent system prompt
   …
   ```

4. Test it at project scope (`./skills/install.sh <name> --project`), start a
   Claude Code session, and give Claude a query that should trigger the skill.
5. Add a row to the table above and commit.

## Why we track these in `skills/` and not in `.claude/skills/`

`.claude/` is per-machine state (settings, worktrees, transcripts) and is
gitignored. Skills authored as part of a paper or repo deserve to live
alongside the code they support — versioned, reviewable, shareable with
collaborators. The symlink indirection lets us have both.
