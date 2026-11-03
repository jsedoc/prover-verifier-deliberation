#!/usr/bin/env bash
# Install the prover-verifier-paper skill and its four subagents for Claude Code.
#
#   ./install.sh              user scope:    ~/.claude/skills and ~/.claude/agents
#   ./install.sh --project    project scope: ./.claude/skills and ./.claude/agents (run from the repo root)
#   ./install.sh --uninstall [--project]
#
# The skill folder stays where it is; this only creates (or removes) symlinks, so edits propagate.
# Inside the prover-verifier-deliberation repo, skills/install.sh prover-verifier-paper does the same.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="prover-verifier-paper"
SCOPE="global"
ACTION="install"
for arg in "$@"; do
  case "$arg" in
    --project) SCOPE="project" ;;
    --global) SCOPE="global" ;;
    --uninstall) ACTION="uninstall" ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 1 ;;
  esac
done

if [[ "$SCOPE" == "global" ]]; then BASE="$HOME/.claude"; else BASE="$(pwd)/.claude"; fi
mkdir -p "$BASE/skills" "$BASE/agents"

if [[ "$ACTION" == "install" ]]; then
  if [[ "$(cd "$BASE/skills" && pwd -P)/$NAME" != "$SRC" ]]; then
    ln -snf "$SRC" "$BASE/skills/$NAME"
    echo "skill  $BASE/skills/$NAME -> $SRC"
  fi
  for a in "$SRC"/agents/*.md; do
    ln -snf "$a" "$BASE/agents/$(basename "$a")"
    echo "agent  $BASE/agents/$(basename "$a")"
  done
  chmod +x "$SRC"/scripts/*.py 2>/dev/null || true
  echo "Done. Start a new Claude Code session; /agents should list pvd-paper-*."
  echo "Optional: pip install pymupdf   (better PDF text, figures, hidden-text scan)"
else
  [[ -L "$BASE/skills/$NAME" ]] && rm "$BASE/skills/$NAME" && echo "removed skill link"
  for a in "$SRC"/agents/*.md; do
    t="$BASE/agents/$(basename "$a")"
    [[ -L "$t" ]] && rm "$t" && echo "removed $t"
  done
fi
