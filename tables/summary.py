"""
Loader for tables/generated/summary.json. Use this in table scripts so
that the same numbers shown in figures are the same numbers in tables.

If summary.json is missing or stale, run:
    python tables/build_summary.py
"""

import json
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SUMMARY_PATH = os.path.join(_THIS_DIR, "generated", "summary.json")


def load_summary() -> list:
    """Read the consolidated summary JSON. Raises FileNotFoundError if absent."""
    if not os.path.exists(SUMMARY_PATH):
        raise FileNotFoundError(
            f"{SUMMARY_PATH} not found. Run `python tables/build_summary.py`."
        )
    with open(SUMMARY_PATH) as f:
        return json.load(f)


def by_key(summary: list | None = None) -> dict:
    """Return summary indexed by `key`."""
    s = summary if summary is not None else load_summary()
    return {r["key"]: r for r in s}


def get(key: str, summary: list | None = None) -> dict:
    """Fetch a single run by key. Raises KeyError if not present."""
    idx = by_key(summary)
    if key not in idx:
        raise KeyError(f"Run '{key}' not in summary.json. "
                       f"Known keys: {sorted(idx)}")
    return idx[key]
