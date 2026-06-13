"""
Parameter robustness heatmaps.

Reads the unified optimizer CSVs (results/optimizer/{preset}/) and plots a
2×3 grid — one row per production preset, three axis pairs each:
  entry × exit  |  entry × drop  |  buy_pct × exit

Each cell = median CAGR of all PASSING combos with that (row, col) pair,
medianed over the remaining free parameters. Blue box = top passing combo.

Usage:
  python param_heatmap.py                # QQQ ma200 + SPY ma100
  python param_heatmap.py --no-show
"""

import argparse
import glob
import os

import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

BASE = os.path.dirname(os.path.abspath(__file__))

# Production exit-MA choice per preset (see README)
PANELS = [
    {"preset": "QQQ", "exit_ma": 200, "label": "QQQ (NASDAQ-100 / TQQQ, MA200 exit)"},
    {"preset": "SPY", "exit_ma": 100, "label": "SPY (S&P 500 / UPRO, MA100 exit)"},
]

SAVE_PATH = os.path.join(BASE, "results", "optimizer", "param_robustness_heatmap.png")

AXIS_PAIRS = [
    ("entry_signal", "exit_signal", "entry × exit"),
    ("entry_signal", "drop_level",  "entry × drop"),
    ("buy_pct",      "exit_signal", "buy% × exit"),
]


def find_csv(preset: str, exit_ma: int, grid: str) -> str:
    pat = os.path.join(BASE, "results", "optimizer", preset,
                       f"{preset}_ma{exit_ma}_*_grid{grid}_results.csv")
    hits = sorted(glob.glob(pat))
    if not hits:
        raise FileNotFoundError(f"No optimizer CSV matches {pat} — "
                                f"run optimizer.py first.")
    return hits[-1]


def pivot_median(df, row_col, col_col):
    return (df.groupby([row_col, col_col])["cagr"].median()
              .reset_index()
              .pivot(index=row_col, columns=col_col, values="cagr"))


def fmt_label(v):
    if isinstance(v, float):
        if abs(v) < 0.9:           # drop levels / buy pcts as %
            return f"{v:.2%}".rstrip("0").rstrip(".") if abs(v) < 0.05 else f"{v:.0%}"
        return f"{v:.2f}×"
    return str(v)


def draw_heatmap(ax, pivot, title, xlabel, ylabel, opt_row, opt_col):
    data = pivot.values.astype(float)
    rows = list(pivot.index)
    cols = list(pivot.columns)

    valid = data[~np.isnan(data)]
    vmin = np.nanpercentile(valid, 10) if valid.size else 0
    vmax = valid.max() if valid.size else 30

    ax.imshow(data, cmap="RdYlGn", aspect="auto", vmin=vmin, vmax=vmax,
              interpolation="nearest")

    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels([fmt_label(c) for c in cols], fontsize=7)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([fmt_label(r) for r in rows], fontsize=7)
    ax.set_xlabel(xlabel, fontsize=8)
    ax.set_ylabel(ylabel, fontsize=8)
    ax.set_title(title, fontsize=9, fontweight="bold", pad=6)

    mid = vmin + (vmax - vmin) * 0.55
    for i in range(len(rows)):
        for j in range(len(cols)):
            val = data[i, j]
            if np.isnan(val):
                ax.text(j, i, "—", ha="center", va="center",
                        fontsize=6, color="#aaa")
            else:
                color = "white" if val < mid else "black"
                ax.text(j, i, f"{val:.1f}", ha="center", va="center",
                        fontsize=6, color=color)

    # Blue box on the chosen optimum
    if opt_row in rows and opt_col in cols:
        ax.add_patch(mpatches.Rectangle(
            (cols.index(opt_col) - 0.5, rows.index(opt_row) - 0.5), 1, 1,
            fill=False, edgecolor="blue", linewidth=2))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--grid", default="v3")
    p.add_argument("--no-show", action="store_true")
    args = p.parse_args()
    if args.no_show:
        matplotlib.use("Agg")

    fig, axes = plt.subplots(len(PANELS), len(AXIS_PAIRS), figsize=(19, 10))

    for r, panel in enumerate(PANELS):
        csv = find_csv(panel["preset"], panel["exit_ma"], args.grid)
        df  = pd.read_csv(csv)
        df  = df[df["passed"] == True].copy()
        best = df.loc[df["cagr"].idxmax()]

        for c, (row_col, col_col, pair_label) in enumerate(AXIS_PAIRS):
            draw_heatmap(
                axes[r][c],
                pivot_median(df, row_col, col_col),
                f"{panel['label']}\n{pair_label}  (median CAGR, passing combos)",
                col_col, row_col,
                best[row_col], best[col_col],
            )

    fig.suptitle(
        "Parameter Robustness Heatmaps — median CAGR of all passing combos\n"
        "Blue box = top passing combo. Wide bright region = plateau (robust). "
        "Isolated bright cell = spike (fragile).",
        fontsize=11)
    plt.tight_layout(rect=(0, 0, 1, 0.94))
    os.makedirs(os.path.dirname(SAVE_PATH), exist_ok=True)
    plt.savefig(SAVE_PATH, dpi=150)
    print(f"Saved: {SAVE_PATH}")
    if not args.no_show:
        plt.show()


if __name__ == "__main__":
    main()
