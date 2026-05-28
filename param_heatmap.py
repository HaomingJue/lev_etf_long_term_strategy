"""
Parameter robustness heatmaps.

Reads the existing optimizer CSVs (15,840 combos each) and plots a 2×2 grid:
  - QQQ: entry_signal × exit_signal  |  entry_signal × drop_level
  - SPY: entry_signal × exit_signal  |  entry_signal × drop_level

Each cell = median CAGR of all PASSING combos with that (row, col) pair,
averaged over the remaining free parameters.  Blue box = chosen optimal.

Usage:
  python param_heatmap.py
  python param_heatmap.py --no-show
"""

import argparse
import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE = os.path.dirname(os.path.abspath(__file__))

PRESETS = {
    "QQQ": {
        "csv":       os.path.join(BASE, "leveraged_qqq_exploration", "ma200", "optimizer_results.csv"),
        "opt_entry": 1.03,
        "opt_exit":  1.01,
        "opt_drop":  0.005,
        "bnh_cagr":  16.16,
        "label":     "QQQ (NASDAQ-100 / TQQQ)",
    },
    "SPY": {
        # SPY MA100 is the optimum (highest-CAGR) full-history config — see §6.1.
        # Per-MA optimizer CSVs live in leveraged_spy_exploration/{ma100,ma200}/.
        "csv":       os.path.join(BASE, "leveraged_spy_exploration", "ma100", "ma100_exit_results.csv"),
        "opt_entry": 1.02,
        "opt_exit":  0.95,
        "opt_drop":  0.005,
        "bnh_cagr":  11.39,
        "label":     "SPY (S&P 500 / UPRO, MA100 exit)",
    },
}

SAVE_DIR = os.path.join(BASE, "results", "walkforward")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load(csv_path):
    df = pd.read_csv(csv_path)
    # Keep only passing combos for the robustness view
    return df[df["passed"] == True].copy()


def pivot_median(df, row_col, col_col):
    """Median CAGR for each (row, col) cell across all other free parameters."""
    return (
        df.groupby([row_col, col_col])["cagr"]
        .median()
        .reset_index()
        .pivot(index=row_col, columns=col_col, values="cagr")
    )


def pivot_passcount(df_all, df_pass, row_col, col_col):
    """Fraction of combos in each cell that pass the DD filter."""
    total = df_all.groupby([row_col, col_col]).size().rename("total")
    passed = df_pass.groupby([row_col, col_col]).size().rename("passed")
    combined = pd.concat([total, passed], axis=1).fillna(0)
    combined["frac"] = combined["passed"] / combined["total"] * 100
    return combined["frac"].reset_index().pivot(index=row_col, columns=col_col, values="frac")


def draw_heatmap(ax, pivot, title, xlabel, ylabel,
                 opt_row, opt_col, bnh_cagr,
                 cmap="RdYlGn", fmt=".1f", unit="%"):
    data = pivot.values.astype(float)
    rows = list(pivot.index)
    cols = list(pivot.columns)

    valid = data[~np.isnan(data)]
    vmin = max(0, bnh_cagr * 0.6) if valid.size else 0
    vmax = valid.max() if valid.size else 30

    im = ax.imshow(data, cmap=cmap, aspect="auto", vmin=vmin, vmax=vmax,
                   interpolation="nearest")

    # Format tick labels
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

    # Annotate each cell
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

    # Blue box around optimal cell
    if opt_row in rows and opt_col in cols:
        ri = rows.index(opt_row)
        ci = cols.index(opt_col)
        ax.add_patch(mpatches.Rectangle(
            (ci - 0.5, ri - 0.5), 1, 1,
            linewidth=2.5, edgecolor="royalblue", facecolor="none", zorder=5
        ))
        ax.text(ci, ri - 0.42, "★", ha="center", va="top",
                fontsize=11, color="royalblue", zorder=6)

    # B&H reference line in colorbar
    cb = plt.colorbar(im, ax=ax, shrink=0.82, pad=0.02)
    cb.set_label(f"Median CAGR{unit} (passing combos)", fontsize=7)

    # Tick the B&H level on the colorbar
    cb_ticks = cb.get_ticks().tolist()
    if bnh_cagr not in cb_ticks:
        cb_ticks.append(bnh_cagr)
    cb.set_ticks(sorted(cb_ticks))

    return im


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(no_show=False):
    os.makedirs(SAVE_DIR, exist_ok=True)

    fig, axes = plt.subplots(2, 2, figsize=(14, 11))
    fig.suptitle(
        "Parameter Robustness Heatmaps  —  Median CAGR of all passing combos\n"
        "Blue box = chosen optimal params.  Wide bright region = plateau (robust).  "
        "Isolated bright cell = spike (fragile).",
        fontsize=10, y=0.99
    )

    for row_idx, (preset_name, cfg) in enumerate(PRESETS.items()):
        df_all  = pd.read_csv(cfg["csv"])
        df_pass = df_all[df_all["passed"] == True].copy()

        label    = cfg["label"]
        bnh      = cfg["bnh_cagr"]
        o_entry  = cfg["opt_entry"]
        o_exit   = cfg["opt_exit"]
        o_drop   = cfg["opt_drop"]

        # --- Panel A: entry × exit (median CAGR) ---
        piv_ee = pivot_median(df_pass, "entry_signal", "exit_signal")
        draw_heatmap(
            axes[row_idx, 0], piv_ee,
            title=f"{label}\nEntry × Exit  (median CAGR, passing combos)",
            xlabel="exit_signal  (exit threshold × MA200)",
            ylabel="entry_signal  (arm threshold × MA200)",
            opt_row=o_entry, opt_col=o_exit,
            bnh_cagr=bnh,
        )

        # --- Panel B: entry × drop_level (median CAGR) ---
        piv_ed = pivot_median(df_pass, "entry_signal", "drop_level")
        # Format drop as %
        piv_ed.columns = [f"{c*100:.1f}%" for c in piv_ed.columns]
        o_drop_label = f"{o_drop*100:.1f}%"

        draw_heatmap(
            axes[row_idx, 1], piv_ed,
            title=f"{label}\nEntry × Drop Level  (median CAGR, passing combos)",
            xlabel="drop_level  (minimum daily dip to trigger buy)",
            ylabel="entry_signal  (arm threshold × MA200)",
            opt_row=o_entry, opt_col=o_drop_label,
            bnh_cagr=bnh,
        )

    plt.tight_layout(rect=[0, 0, 1, 0.97])

    save_path = os.path.join(SAVE_DIR, "param_robustness_heatmap.png")
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    print(f"  Saved: {save_path}")

    if not no_show:
        plt.show()
    plt.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-show", action="store_true")
    args = parser.parse_args()
    main(no_show=args.no_show)
