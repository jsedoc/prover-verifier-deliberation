"""
Run all table reproduction scripts in sequence.

Usage (from worktree root):
    python tables/make_all_tables.py
    python tables/make_all_tables.py --table 3        # only Table 3
    python tables/make_all_tables.py --table 3 5 8    # Tables 3, 5, and 8

Each script is self-contained and produces one paper table.
"""

import sys
import os
import importlib
import argparse

TABLE_MODULES = {
    3: ("tables.table3_gpqa_main",    "Table 3 — GPQA Diamond: Main Results"),
    4: ("tables.table4_gpqa_domain",  "Table 4 — GPQA Diamond: ANC by Domain"),
    5: ("tables.table5_hle_domain",   "Table 5 — HLE: ANC by Domain"),
    6: ("tables.table6_verifier",     "Table 6 — Verifier Choice Effect"),
    7: ("tables.table7_hle_capability", "Table 7 — HLE ANC Gap by Model Pairing"),
    8: ("tables.table8_sc_pvd",       "Table 8 — SC vs PVD Overlap"),
    9: ("tables.table9_rttr_stats",   "Table 9 — Clean RTTR Runs with 95% CIs"),
}


def run_table(num: int):
    module_path, description = TABLE_MODULES[num]
    print(f"\n{'='*70}")
    print(f"  {description}")
    print(f"{'='*70}\n")
    try:
        # Add worktree root to path so `tables.utils` resolves
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        mod = importlib.import_module(module_path)
        mod.main()
    except FileNotFoundError as e:
        print(f"  [MISSING DATA] {e}")
    except Exception as e:
        import traceback
        print(f"  [ERROR] {e}")
        traceback.print_exc()


def _refresh_summary():
    """Rebuild tables/generated/summary.json before running any table."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    print(f"\n{'='*70}")
    print(f"  Refreshing tables/generated/summary.json")
    print(f"{'='*70}\n")
    try:
        mod = importlib.import_module("tables.build_summary")
        mod.main()
    except Exception as e:
        import traceback
        print(f"  [ERROR refreshing summary] {e}")
        traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(description="Reproduce all paper tables")
    parser.add_argument("--table", nargs="*", type=int,
                        help="Table number(s) to run (default: all)")
    parser.add_argument("--skip-summary", action="store_true",
                        help="Don't rebuild summary.json before running tables")
    args = parser.parse_args()

    tables_to_run = args.table if args.table else sorted(TABLE_MODULES)
    invalid = [t for t in tables_to_run if t not in TABLE_MODULES]
    if invalid:
        print(f"Unknown table number(s): {invalid}")
        print(f"Available: {sorted(TABLE_MODULES)}")
        sys.exit(1)

    if not args.skip_summary:
        _refresh_summary()

    for num in tables_to_run:
        run_table(num)

    print(f"\n{'='*70}")
    print("  All tables complete.")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
