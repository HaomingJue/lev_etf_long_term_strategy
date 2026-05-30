"""
Parameter robustness heatmap for the SPY shifted-grid sweep.

Reads leveraged_spy_exploration/ma100_shifted/ma100_shifted_results.csv
and renders the same 2-panel layout used by param_heatmap.py
(entry × exit, entry × drop) so a reader can visually compare against
the production-grid heatmap in §8.

Output: leveraged_spy_exploration/ma100_shifted/heatmap_shifted.png

Run:
  python heatmap_shifted.py
  python heatmap_shifted.py --no-show
"""
import argparse
import os
import sys
from pathlib import Path

import matplotlib
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd

HERE = Path(__file__).parent
CSV  = HERE / "ma100_shifted" / "ma100_shifted_results.csv"
OUT  = HERE / "ma100_shifted" / "heatmap_shifted.png"

OPT_ENTRY = 1.02
OPT_EXIT  = 0.95
OPT_DROP  = 0.005
BNH_CAGR  = 11.39   # SPY full-history B&H CAGR, same baseline as production heatmap


def pivot_median(df, row_col, col_col):
    return (df.groupby([row_col, col_col])["cagr"]
              .median()
              .reset_index()
              .pivot(index=row_col, columns=col_col, values="cagr"))


def draw_heatmap(ax, pivot, title, xlabel, ylabel,
                 opt_row, opt_col, bnh_cagr,
                 cmap="RdYlGn", fmt=".1f", unit="%"):
    import matplotlib.pyplot as plt
    data = pivot.values.astype(float)
    rows = list(pivot.index)
    cols = list(pivot.columns)

    valid = data[~np.isnan(data)]
    vmin = max(0, bnh_cagr * 0.6) if valid.size else 0
    vmax = valid.max() if valid.size else 30

    im = ax.imshow(data, cmap=cmap, aspect="auto", vmin=vmin, vmax=vmax,
                   interpolation="nearest")

    def fmt_label(v):
        if isinstance(v, float):
            return f"{v:.0%}" if v < 1 else f"{v:.2f}×"
        return str(v)

    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels([fmt_label(c) for c in cols], fontsize=8)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([fmt_label(r) for r in rows], fontsize=8)
    ax.set_xlabel(xlabel, fontsize=9)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.set_title(title, fontsize=10, fontweight="bold", pad=6)

    mid = vmin + (vmax - vmin) * 0.55
    for i, r in enumerate(rows):
        for j, c in enumerate(cols):
            val = data[i, j]
            if np.isnan(val):
                ax.text(j, i, "—", ha="center", va="center", fontsize=7, color="#aaa")
            else:
                color = "white" if val < mid else "black"
                ax.text(j, i, f"{val:{fmt}}{unit}", ha="center", va="center",
                        fontsize=7, color=color)

    if opt_row in rows and opt_col in cols:
        ri = rows.index(opt_row)
        ci = cols.index(opt_col)
        ax.add_patch(mpatches.Rectangle(
            (ci - 0.5, ri - 0.5), 1, 1,
            linewidth=2.5, edgecolor="royalblue", facecolor="none", zorder=5,
        ))
        ax.text(ci, ri - 0.42, "★", ha="center", va="top",
                fontsize=11, color="royalblue", zorder=6)

    cb = plt.colorbar(im, ax=ax, shrink=0.82, pad=0.02)
    cb.set_label(f"Median CAGR{unit} (passing combos)", fontsize=7)

    cb_ticks = cb.get_ticks().tolist()
    if bnh_cagr not in cb_ticks:
        cb_ticks.append(bnh_cagr)
    cb.set_ticks(sorted(cb_ticks))


def main(no_show=False):
    if no_show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    df_all  = pd.read_csv(CSV)
    df_pass = df_all[df_all["passed"] == True].copy()
    print(f"  Loaded {CSV.name}: {len(df_all):,} combos, "
          f"{len(df_pass):,} passing")

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
    fig.suptitle(
        "SPY (MA100) — Parameter Robustness on SHIFTED GRID\n"
        "Median CAGR of passing combos. Blue box ★ = chosen optimum "
        "(entry=1.02, exit=0.95, drop=0.5%). "
        "Compare against the production-grid heatmap in §8 of README.",
        fontsize=10, y=1.0,
    )

    # Panel A: entry × exit
    piv_ee = pivot_median(df_pass, "entry_signal", "exit_signal")
    draw_heatmap(
        axes[0], piv_ee,
        title="Entry × Exit  (median CAGR, passing combos)",
        xlabel="exit_signal  (× MA100)",
        ylabel="entry_signal  (× MA200)",
        opt_row=OPT_ENTRY, opt_col=OPT_EXIT,
        bnh_cagr=BNH_CAGR,
    )

    # Panel B: entry × drop_level
    piv_ed = pivot_median(df_pass, "entry_signal", "drop_level")
    piv_ed.columns = [f"{c*100:.1f}%" for c in piv_ed.columns]
    opt_drop_label = f"{OPT_DROP*100:.1f}%"

    draw_heatmap(
        axes[1], piv_ed,
        title="Entry × Drop Level  (median CAGR, passing combos)",
        xlabel="drop_level  (minimum daily dip to trigger buy)",
        ylabel="entry_signal  (× MA200)",
        opt_row=OPT_ENTRY, opt_col=opt_drop_label,
        bnh_cagr=BNH_CAGR,
    )

    plt.tight_layout(rect=[0, 0, 1, 0.93])
    plt.savefig(OUT, dpi=150, bbox_inches="tight")
    print(f"  Saved: {OUT}")
    if not no_show:
        plt.show()
    plt.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-show", action="store_true")
    args = parser.parse_args()
    main(no_show=args.no_show)
