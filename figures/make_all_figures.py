"""
Render the figure used by neurips_paper.tex from tables/generated/rttr_summary.json.

Usage (from worktree root):
    python -m rttr.summary              # refresh data
    python figures/make_all_figures.py

This runs:
    fig_rttr_cost.py       -> fig_rttr_cost.png
"""

import importlib
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

FIGURES = [
    ("fig_rttr_cost", "fig_rttr_cost"),
]


def main():
    for mod_name, out_base in FIGURES:
        print(f"\n=== {mod_name} -> {out_base}.png ===")
        try:
            mod = importlib.import_module(mod_name)
            mod.main()
        except Exception as e:
            import traceback
            print(f"  [ERROR] {e}")
            traceback.print_exc()
    print("\nAll figures complete.")


if __name__ == "__main__":
    main()
