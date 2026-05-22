"""
figures/fig_rttr_cost.png -- HC precision vs cost for RTTR runs.

X-axis  : mean cents per question (lower is cheaper)
Y-axis  : HC precision (accuracy on the high-confidence subset)
Bubble  : HC coverage (larger means more questions reported)
Color   : method family

All numbers come from tables/generated/rttr_summary.json.

Run from the repo root:
    python -m rttr.summary
    python figures/fig_rttr_cost.py
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.ticker import FixedFormatter, FixedLocator


HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SUMMARY = REPO / "tables" / "generated" / "rttr_summary.json"

BLUE = "#4C72B0"
ORANGE = "#DD8452"
GREEN = "#2CA02C"
PURPLE = "#8E44AD"
GREY = "#888888"

KEY_STYLE = {
    "rttr_pvd_standard": dict(color=ORANGE, marker="o", label="PVD"),
    "rttr_pvd_min1": dict(color=ORANGE, marker="D", label="PVD min-1"),
    "rttr_pvd_self": dict(color=GREEN, marker="o", label="PVD self"),
    "rttr_pvd_retry": dict(color=ORANGE, marker="s", label="PVD+retry"),
    "rttr_debate": dict(color=BLUE, marker="P", label="Debate"),
    "rttr_reflexion": dict(color=BLUE, marker="v", label="Reflexion"),
    "rttr_sc_epoch": dict(color=BLUE, marker="s", label="SC"),
    "rttr_usc": dict(color=BLUE, marker="^", label="USC"),
    "rttr_single_call": dict(color=PURPLE, marker="*", label="Single-call PVD"),
}

FAMILY_LEGEND = [
    ("Baselines", BLUE),
    ("PVD: Sonnet/Haiku", ORANGE),
    ("PVD: Sonnet/Sonnet", GREEN),
    ("Single-call self-deliberation", PURPLE),
]

LABEL_OFFSETS = {
    "rttr_pvd_standard": (8, 6, "left"),
    "rttr_pvd_min1": (-8, -8, "right"),
    "rttr_pvd_self": (8, 8, "left"),
    "rttr_pvd_retry": (14, -14, "left"),
    "rttr_debate": (-10, -16, "right"),
    "rttr_reflexion": (10, -16, "left"),
    "rttr_sc_epoch": (-14, 10, "right"),
    "rttr_usc": (14, -8, "left"),
    "rttr_single_call": (8, 2, "left"),
}


def load_runs() -> list[dict]:
    with SUMMARY.open() as f:
        return json.load(f)


def bubble_size(cov_pct: float) -> float:
    cov = max(0.0, min(100.0, cov_pct or 0.0))
    return 60.0 + 600.0 * (cov / 100.0)


def main() -> None:
    runs = load_runs()

    fig, ax = plt.subplots(figsize=(7.5, 5.0))
    for run in runs:
        style = KEY_STYLE.get(run["key"], dict(color=GREY, marker="o", label=run["key"]))
        x = 100.0 * run["cost_usd"]
        y = run["hc_prec"]
        ax.scatter(
            [x],
            [y],
            s=bubble_size(run["hc_cov"]),
            color=style["color"],
            marker=style["marker"],
            alpha=0.88,
            edgecolors="white",
            linewidths=0.9,
            zorder=4,
        )
        xoff, yoff, ha = LABEL_OFFSETS.get(run["key"], (8, 2, "left"))
        ax.annotate(
            style["label"],
            (x, y),
            textcoords="offset points",
            xytext=(xoff, yoff),
            fontsize=7.5,
            ha=ha,
            va="center",
            color="#222222",
        )

    ax.set_xscale("log")
    costs_cents = [100.0 * run["cost_usd"] for run in runs]
    ax.set_xlim(min(costs_cents) / 1.2, max(costs_cents) * 1.35)
    tick_values = [3, 4, 5, 7, 10, 15, 20, 30, 50, 75, 100]
    tick_values = [t for t in tick_values if min(costs_cents) / 1.2 <= t <= max(costs_cents) * 1.35]
    ax.xaxis.set_major_locator(FixedLocator(tick_values))
    ax.xaxis.set_major_formatter(FixedFormatter([f"{t}c" for t in tick_values]))
    ax.minorticks_off()
    ax.set_xlabel("Mean cents per question (log scale)", fontsize=10)
    ax.set_ylabel("HC precision: accuracy on reported subset (%)", fontsize=10)
    ax.set_ylim(78, 102)
    ax.set_yticks(range(80, 101, 4))
    ax.grid(axis="both", color="#e8e8e8", linewidth=0.7, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", labelsize=9)
    ax.set_title("HC precision vs cost in cents (bubble area = HC coverage)", fontsize=11, pad=8)

    family_handles = [mpatches.Patch(color=color, label=label) for label, color in FAMILY_LEGEND]
    family_legend = ax.legend(
        handles=family_handles,
        fontsize=8,
        title="Method family",
        title_fontsize=8,
        framealpha=0.9,
        loc="lower right",
    )
    ax.add_artist(family_legend)

    cov_handles = [
        ax.scatter([], [], s=bubble_size(v), color="#aaaaaa", alpha=0.75, label=f"{v}%")
        for v in (40, 65, 90)
    ]
    ax.legend(
        handles=cov_handles,
        fontsize=8,
        title="HC coverage",
        title_fontsize=8,
        framealpha=0.9,
        loc="upper right",
        bbox_to_anchor=(0.99, 88),
        bbox_transform=ax.get_yaxis_transform(),
    )

    plt.tight_layout()
    out = HERE / "fig_rttr_cost.png"
    fig.savefig(out, bbox_inches="tight", dpi=200)
    print(f"  -> {out}")
    plt.close(fig)


if __name__ == "__main__":
    main()
