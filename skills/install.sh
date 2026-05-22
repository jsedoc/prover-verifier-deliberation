#!/usr/bin/env bash
# Install or uninstall a skill (and its bundled agents) into ~/.claude or .claude/.
#
# Usage:
#   ./skills/install.sh <skill-name> [--global | --project] [--uninstall]
#
# Defaults to --global. The skill source under skills/<name>/ stays put; this
# script only manages the symlinks that activate it for Claude Code.

set -euo pipefail

SCOPE=""
ACTION="install"
SKILL=""

usage() {
  cat <<EOF
Usage: $0 <skill-name> [--global | --project] [--uninstall]

Symlinks skills/<skill-name>/SKILL.md and skills/<skill-name>/agents/*.md
into the active Claude Code location.

  --global     ~/.claude/skills/ and ~/.claude/agents/   (default)
  --project    .claude/skills/  and .claude/agents/      (per-repo)
  --uninstall  remove the symlinks (source in skills/ stays)
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --global)    SCOPE="global"; shift ;;
    --project)   SCOPE="project"; shift ;;
    --uninstall) ACTION="uninstall"; shift ;;
    -h|--help)   usage ;;
    *)
      if [[ -z "$SKILL" ]]; then
        SKILL="$1"; shift
      else
        echo "Unknown argument: $1" >&2; usage
      fi
      ;;
  esac
done

[[ -z "$SKILL" ]] && usage
[[ -z "$SCOPE" ]] && SCOPE="global"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="$REPO_ROOT/skills/$SKILL"

if [[ ! -d "$SRC_DIR" ]]; then
  echo "No such skill: $SRC_DIR" >&2
  exit 1
fi
if [[ ! -f "$SRC_DIR/SKILL.md" ]]; then
  echo "Missing $SRC_DIR/SKILL.md" >&2
  exit 1
fi

if [[ "$SCOPE" == "global" ]]; then
  DEST_SKILLS="$HOME/.claude/skills"
  DEST_AGENTS="$HOME/.claude/agents"
else
  DEST_SKILLS="$REPO_ROOT/.claude/skills"
  DEST_AGENTS="$REPO_ROOT/.claude/agents"
fi

if [[ "$ACTION" == "install" ]]; then
  mkdir -p "$DEST_SKILLS" "$DEST_AGENTS"
  ln -snf "$SRC_DIR" "$DEST_SKILLS/$SKILL"
  echo "Linked skill:  $DEST_SKILLS/$SKILL -> $SRC_DIR"

  if [[ -d "$SRC_DIR/agents" ]]; then
    shopt -s nullglob
    for a in "$SRC_DIR"/agents/*.md; do
      ln -snf "$a" "$DEST_AGENTS/$(basename "$a")"
      echo "Linked agent:  $DEST_AGENTS/$(basename "$a") -> $a"
    done
    shopt -u nullglob
  fi
else
  rm -f "$DEST_SKILLS/$SKILL"
  echo "Removed skill link: $DEST_SKILLS/$SKILL"
  if [[ -d "$SRC_DIR/agents" ]]; then
    shopt -s nullglob
    for a in "$SRC_DIR"/agents/*.md; do
      rm -f "$DEST_AGENTS/$(basename "$a")"
      echo "Removed agent link: $DEST_AGENTS/$(basename "$a")"
    done
    shopt -u nullglob
  fi
fi
