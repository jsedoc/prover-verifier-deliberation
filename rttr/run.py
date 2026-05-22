"""
Single CLI entry point: dispatch to the right protocol module based on
the `protocol` field of the YAML config.

Usage:
    python -m rttr.run --config configs/<name>.yaml
    python -m rttr.run --config configs/<name>.yaml --n 10  # override dataset.n
"""

from __future__ import annotations

import argparse
import sys

from rttr.common import load_config


PROTOCOL_MODULES = {
    "pvd":         "rttr.pvd",
    "debate":      "rttr.debate",
    "reflexion":   "rttr.reflexion",
    "usc":         "rttr.usc",
    "single_call": "rttr.single_call",
}


def main():
    p = argparse.ArgumentParser(description="Run an RTTR evaluation.")
    p.add_argument("--config", required=True, help="Path to YAML config")
    p.add_argument("--n", type=int, default=None,
                   help="Override dataset.n (useful for pilots)")
    p.add_argument("--output-suffix", default=None,
                   help="Append a suffix to the output filename (before .json)")
    args = p.parse_args()

    cfg = load_config(args.config)

    # --n override
    if args.n is not None:
        cfg["dataset"]["n"] = args.n

    # --output-suffix override
    if args.output_suffix:
        out_abs = cfg["logging"]["_output_abs"]
        if out_abs.endswith(".json"):
            out_abs = out_abs[:-len(".json")] + args.output_suffix + ".json"
        else:
            out_abs = out_abs + args.output_suffix
        cfg["logging"]["_output_abs"] = out_abs

    proto = cfg["protocol"]
    if proto not in PROTOCOL_MODULES:
        print(f"Unknown protocol: {proto!r}. Choices: {list(PROTOCOL_MODULES)}",
              file=sys.stderr)
        sys.exit(1)

    import importlib
    mod = importlib.import_module(PROTOCOL_MODULES[proto])
    mod.main(cfg)


if __name__ == "__main__":
    main()
