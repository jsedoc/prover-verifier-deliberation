"""
RTTR — Re-run, Token-accounted, Release package.

All evaluation protocols (PVD variants, Debate, Reflexion) live here.
Each protocol is driven by a YAML config in configs/ and writes a result
JSON in data/ following the rttr-v1 schema summarized in README.md.

Entry point:
    python -m rttr.run --config configs/<name>.yaml
"""

SCHEMA_VERSION = "rttr-v1"
