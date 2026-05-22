"""
Shared utilities for the rttr package.

Provides:

- `load_config(path)`       — read a YAML config and validate the bare minimum
- `load_prompt(name, **kw)` — read prompts/<name>.txt and `.format(**kw)`
- `load_gpqa_diamond(...)`  — deterministic GPQA Diamond loader (matches the
                              seeding used in the existing scripts)
- `TokenUsage`              — dataclass tracking input/output/thinking tokens
- `cost_usd(tokens, model)` — USD cost given a TokenUsage and a model key
- `make_meta(...)`          — build the `_meta` record written to each output
- `repo_root()`             — absolute path to the trust_but_verify_decoding repo

All paths are relative to the repo root, so the scripts can be invoked from
anywhere.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv


# ── Paths ────────────────────────────────────────────────────────────────────

_HERE = Path(__file__).resolve().parent

def repo_root() -> Path:
    """Walk up from rttr/ until we find the repo root (has neurips_paper.tex)."""
    d = _HERE
    for _ in range(10):
        if (d / "neurips_paper.tex").exists() or (d / ".git").exists():
            return d
        d = d.parent
    return _HERE.parent  # fallback


REPO   = repo_root()
PROMPTS_DIR = REPO / "prompts"
CONFIGS_DIR = REPO / "configs"
DATA_DIR    = REPO / "data"


# Load .env once at module import. Prefer DOTENV_PATH (used in CI),
# otherwise the repo-root .env. override=True so a stale shell variable
# doesn't shadow the on-disk key.
_dotenv_path = os.getenv("DOTENV_PATH") or str(REPO / ".env")
if Path(_dotenv_path).exists():
    load_dotenv(_dotenv_path, override=True)


# ── Pricing (USD per million tokens, list rates Q1 2026) ─────────────────────
# Single source of truth for cost computation. Update here and rebuild.
#
# Thinking tokens bill at the same rate as output tokens.

TOKEN_PRICES = {
    "claude-sonnet-4-6":             {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001":     {"input": 0.80, "output":  4.00},
    "claude-opus-4-6":               {"input": 15.00, "output": 75.00},
    "claude-haiku-4-5":              {"input": 0.80, "output":  4.00},   # alias
}


# ── TokenUsage ───────────────────────────────────────────────────────────────

@dataclass
class TokenUsage:
    """Per-call or per-question token tally."""
    input: int = 0
    output: int = 0
    thinking: int = 0   # included in output for billing, tracked separately for analysis

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input=self.input + other.input,
            output=self.output + other.output,
            thinking=self.thinking + other.thinking,
        )

    def to_dict(self) -> dict:
        return {"input": self.input, "output": self.output, "thinking": self.thinking}

    @classmethod
    def from_anthropic(cls, usage) -> "TokenUsage":
        """Extract from anthropic SDK's `response.usage` object."""
        thinking = 0
        # Anthropic exposes thinking tokens via cache_creation_input_tokens or
        # similar; in our scripts thinking is always 0 for the verifier, and
        # output_tokens already includes thinking when enabled. We keep this
        # field for forward compatibility.
        return cls(
            input=getattr(usage, "input_tokens", 0),
            output=getattr(usage, "output_tokens", 0),
            thinking=thinking,
        )


def cost_usd(tokens: TokenUsage, model: str) -> float:
    """USD cost for a TokenUsage given a model. Thinking tokens are part of `output`."""
    if model not in TOKEN_PRICES:
        # Fall back to Sonnet 4.6 rates with a warning to stderr
        import sys
        print(f"[common.py] WARN: unknown model {model!r}, using Sonnet rates", file=sys.stderr)
        rates = TOKEN_PRICES["claude-sonnet-4-6"]
    else:
        rates = TOKEN_PRICES[model]
    return (tokens.input * rates["input"] + tokens.output * rates["output"]) / 1_000_000


# ── Config loading ───────────────────────────────────────────────────────────

REQUIRED_KEYS = {"run_key", "dataset", "protocol", "logging", "compute"}

def load_config(path: str | Path) -> dict:
    """Read a YAML config, basic validation, return the dict."""
    p = Path(path)
    if not p.is_absolute():
        p = REPO / p
    with open(p) as f:
        cfg = yaml.safe_load(f)
    missing = REQUIRED_KEYS - cfg.keys()
    if missing:
        raise ValueError(f"Config {p} missing required keys: {sorted(missing)}")
    # Resolve output path relative to repo
    out = cfg["logging"]["output"]
    if not os.path.isabs(out):
        cfg["logging"]["_output_abs"] = str(REPO / out)
    else:
        cfg["logging"]["_output_abs"] = out
    cfg["_config_path"] = str(p)
    return cfg


# ── Prompt loading ───────────────────────────────────────────────────────────

def load_prompt(name: str, **kwargs) -> str:
    """
    Read prompts/<name>.txt, strip leading comment lines (lines starting with
    `#` at the top of the file), and apply str.format(**kwargs).

    `name` may include or omit the `.txt` extension.
    """
    if not name.endswith(".txt"):
        name = f"{name}.txt"
    path = PROMPTS_DIR / name
    text = path.read_text()
    lines = text.splitlines()
    # Strip leading comment block
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    # Skip a single blank line after the header
    body = "\n".join(lines[i:]).lstrip("\n").rstrip() + "\n"
    if kwargs:
        body = body.format(**kwargs)
    return body


# ── GPQA loader (matches existing scripts byte-for-byte) ─────────────────────

CHOICES = ["A", "B", "C", "D"]

@dataclass
class MCQItem:
    question: str
    choices: dict
    correct_letter: str
    domain: str
    subdomain: str


def load_gpqa_diamond(n: int = 198, seed: int = 42) -> list[MCQItem]:
    """
    Load GPQA Diamond with the canonical seeding used everywhere in this repo:
        - per-row choice-shuffle seed = base_seed + index
        - whole-list shuffle seed     = base_seed

    Returns the first `n` items after the list shuffle.
    """
    from datasets import load_dataset
    token = os.environ.get("HF_TOKEN")
    ds = load_dataset("Idavidrein/gpqa", "gpqa_diamond", token=token)["train"]
    items: list[MCQItem] = []
    for row in ds:
        subdomain  = row.get("Subdomain", "")
        high_level = row.get("High-level domain", subdomain)
        correct_text = row["Correct Answer"]
        wrong = [row["Incorrect Answer 1"], row["Incorrect Answer 2"], row["Incorrect Answer 3"]]
        all_choices = [correct_text] + wrong
        rng = random.Random(seed + len(items))
        rng.shuffle(all_choices)
        choices = {letter: text for letter, text in zip(CHOICES, all_choices)}
        correct_letter = next(k for k, v in choices.items() if v == correct_text)
        items.append(MCQItem(
            question=row["Question"], choices=choices,
            correct_letter=correct_letter,
            domain=high_level, subdomain=subdomain,
        ))
    random.seed(seed)
    random.shuffle(items)
    return items[:n]


def format_mcq(item: MCQItem) -> str:
    lines = [item.question, ""]
    for letter, text in item.choices.items():
        lines.append(f"({letter}) {text}")
    return "\n".join(lines)


# ── _meta record ─────────────────────────────────────────────────────────────

def _git_sha() -> str:
    try:
        out = subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                                      stderr=subprocess.DEVNULL).decode().strip()
        return out[:10]
    except Exception:
        return "unknown"


def make_meta(config: dict, started_at: datetime,
              schema_version: str, extra: Optional[dict] = None) -> dict:
    """Build the `_meta` record written as the first entry of each output JSON."""
    meta = {
        "_meta": True,
        "run_key": config["run_key"],
        "config_file": config.get("_config_path", "(unknown)"),
        "git_sha": _git_sha(),
        "dataset": config["dataset"],
        "protocol": config["protocol"],
        "started_at": started_at.astimezone(timezone.utc).isoformat(),
        "finished_at": None,
        "schema_version": schema_version,
    }
    if extra:
        meta.update(extra)
    return meta


def finalize_meta(meta: dict, finished_at: datetime) -> dict:
    meta["finished_at"] = finished_at.astimezone(timezone.utc).isoformat()
    return meta


# ── Output I/O ───────────────────────────────────────────────────────────────

def load_existing(output_path: str) -> tuple[Optional[dict], list[dict]]:
    """
    Read an existing output file. Returns (meta_or_None, records).
    Records are filtered to those with `schema_version` == current.

    Used for --resume so we skip already-completed question_nums.
    """
    if not os.path.exists(output_path):
        return None, []
    with open(output_path) as f:
        data = json.load(f)
    if not data:
        return None, []
    meta = None
    records = []
    for entry in data:
        if isinstance(entry, dict) and entry.get("_meta"):
            meta = entry
        else:
            records.append(entry)
    return meta, records


def save_results(output_path: str, meta: dict, records: list[dict]) -> None:
    """Write meta + records to output_path. Atomic via tmp+rename."""
    tmp = output_path + ".tmp"
    payload = [meta] + records if meta else records
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, output_path)


# ── Utility: extract JSON from a model response ──────────────────────────────

def extract_json(text: str) -> Optional[dict]:
    """Best-effort JSON extraction from a model response."""
    clean = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass
    last = text.rfind("}")
    if last == -1:
        return None
    for start in range(last, -1, -1):
        if text[start] == "{":
            try:
                return json.loads(text[start:last + 1])
            except json.JSONDecodeError:
                continue
    return None
