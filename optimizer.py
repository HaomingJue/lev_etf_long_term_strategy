"""
optimizer.py — unified grid-search CLI for all presets and exit MAs.

Replaces the old per-index scripts (leveraged_{qqq,spy,iwm}_exploration/
optimizer*.py). The engine, data pipeline (incl. MER correction) and grid
live in optimizer_core.py and are shared with walkforward.py, so the
standalone optimizer and the walk-forward re-opt always agree.

Usage:
  python optimizer.py --preset QQQ                       # full history, MA200 exit
  python optimizer.py --preset SPY --exit-ma 100
  python optimizer.py --preset QQQ --end 2014-12-31      # training window only
  python optimizer.py --preset IWM --exit-ma 50 --no-show

Output (results/optimizer/{preset}/):
  {slug}_results.csv   one row per combo (pass/fail flagged)
  {slug}_summary.txt   leaderboard + best-combo year-by-year DD check
  {slug}_equity.png    top-5 equity curves vs B&H
  {slug}_scatter.png   CAGR vs worst-year scatter
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_no_show = "--no-show" in sys.argv
import matplotlib
if _no_show:
    matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

sys.path.insert(0, str(Path(__file__).parent))
from optimizer_core import (CAPITAL, DD_LIMIT, DEFAULT_END, DEFAULT_GRID,
                            GRID_AXES, PRESETS, START_DATE, build_grid,
                            check_dd, load_full_data, opt_backtest,
                            run_grid_search)


# ──────────────────────────────────────────────────────────────
# PLOTS
# ──────────────────────────────────────────────────────────────

def plot_top_combos(df, results, base_tk, exit_ma, save_path, no_show, n=5):
    top = results[results["passed"]].nlargest(n, "cagr")
    fig, ax = plt.subplots(figsize=(14, 7))

    bh = CAPITAL * df["base"] / df["base"].iloc[0]
    ax.plot(df.index, bh, label=f"{base_tk} Buy & Hold",
            color="steelblue", linewidth=1.8, linestyle="--")

    colors = plt.cm.tab10(np.linspace(0, 0.7, n))
    for rank, (_, row) in enumerate(top.iterrows()):
        _, port = opt_backtest(df, row["entry_signal"], row["drop_level"],
                               row["exit_signal"], row["buy_pct"],
                               row["alloc_base"], row["alloc_x2"],
                               exit_ma=exit_ma)
        label = (f"#{rank+1}  e={row['entry_signal']} d={row['drop_level']} "
                 f"x={row['exit_signal']} b={row['buy_pct']}  "
                 f"base={row['alloc_base']} x2={row['alloc_x2']}  "
                 f"CAGR={row['cagr']:.1f}% worst={row['worst_ann_ret']:.1f}%")
        ax.plot(df.index, port, label=label, color=colors[rank], linewidth=1.2)

    ax.set_title(f"Top combos — {base_tk} (exit MA{exit_ma}) equity curves",
                 fontsize=11)
    ax.set_ylabel("Portfolio Value ($)")
    ax.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax.legend(fontsize=7, loc="upper left")
    ax.grid(True, alpha=0.35)
    plt.tight_layout()
    plt.savefig(save_path, dpi=150)
    print(f"  Saved: {save_path}")
    if not no_show:
        plt.show(block=True)
    plt.close()


def plot_scatter(results, base_tk, exit_ma, save_path, no_show):
    fig, ax = plt.subplots(figsize=(10, 6))
    fail  = results[~results["passed"]]
    pass_ = results[ results["passed"]]
    ax.scatter(fail["worst_ann_ret"],  fail["cagr"],
               alpha=0.15, s=6, color="tomato",  label="fail")
    ax.scatter(pass_["worst_ann_ret"], pass_["cagr"],
               alpha=0.4,  s=6, color="seagreen", label="pass")
    ax.axvline(-DD_LIMIT * 100, color="black", linestyle="--", linewidth=1,
               label=f"-{DD_LIMIT*100:.0f}% annual return cap")
    ax.set_xlabel("Worst Annual Return (%)")
    ax.set_ylabel("CAGR (%)")
    ax.set_title(f"CAGR vs Worst Annual Return — {base_tk} exit MA{exit_ma}, "
                 f"all combos")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(save_path, dpi=150)
    print(f"  Saved: {save_path}")
    if not no_show:
        plt.show(block=True)
    plt.close()


# ──────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(
        description="Unified full-history / windowed grid-search optimizer",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--preset",  default="QQQ", choices=list(PRESETS))
    p.add_argument("--exit-ma", type=int, default=200, choices=[50, 100, 200],
                   help="MA period for the exit signal (arm always uses MA200)")
    p.add_argument("--grid",    default=DEFAULT_GRID, choices=list(GRID_AXES),
                   help="Grid version (see optimizer_core.GRID_AXES)")
    p.add_argument("--end",     default=DEFAULT_END,
                   help="Data end date, exclusive (use e.g. 2014-12-31 for the "
                        "training-window study)")
    p.add_argument("--workers", type=int,
                   default=max(1, (os.cpu_count() or 4) - 2),
                   help="Parallel worker processes")
    p.add_argument("--top",     type=int, default=20,
                   help="Leaderboard rows to print/save")
    p.add_argument("--no-show", action="store_true",
                   help="Suppress interactive plot windows")
    return p.parse_args()


def main():
    args     = _parse_args()
    dd_start = PRESETS[args.preset]["dd_start"]
    base_tk  = PRESETS[args.preset]["base"]

    df = load_full_data(args.preset, end=args.end)
    print(f"\nData range  : {df.index[0].date()} -> {df.index[-1].date()}")
    print(f"Trading days: {len(df)}")
    print(f"DD filter   : annual return >= -{DD_LIMIT*100:.0f}% "
          f"from {dd_start} onward")

    grid = build_grid(args.grid)
    print(f"Grid {args.grid}     : {len(grid):,} combos, "
          f"{args.workers} worker(s)\n")

    results = run_grid_search(df, grid, args.exit_ma, dd_start,
                              workers=args.workers,
                              desc=f"{args.preset} MA{args.exit_ma}")

    out_dir = Path(__file__).parent / "results" / "optimizer" / args.preset
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = (f"{args.preset}_ma{args.exit_ma}_"
            f"{df.index[0].year}-{df.index[-1].year}_grid{args.grid}")

    results.to_csv(out_dir / f"{slug}_results.csv", index=False)
    print(f"\n  Saved: {out_dir / (slug + '_results.csv')} "
          f"({len(results):,} rows)")

    passing = results[results["passed"]].sort_values("cagr", ascending=False)
    print(f"  Passing combos: {len(passing):,} / {len(results):,}")

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 140)
    header = (f"LEADERBOARD — {args.preset} exit MA{args.exit_ma}, "
              f"grid {args.grid}, {df.index[0].date()} -> {df.index[-1].date()}\n"
              f"Filter: annual return >= -{DD_LIMIT*100:.0f}% "
              f"from {dd_start} onward\n")
    board = passing.head(args.top).to_string(index=False)
    print("\n" + "=" * 110)
    print(header)
    print(board)

    summary_lines = [header, board, ""]

    if not passing.empty:
        best = passing.iloc[0]
        _, port = opt_backtest(df, best["entry_signal"], best["drop_level"],
                               best["exit_signal"], best["buy_pct"],
                               best["alloc_base"], best["alloc_x2"],
                               exit_ma=args.exit_ma)
        bh_days = (df.index[-1] - df.index[0]).days
        bh_cagr = ((df["base"].iloc[-1] / df["base"].iloc[0])
                   ** (365.25 / bh_days) - 1) * 100

        best_block = [
            "=" * 110,
            "BEST COMBO",
            f"  entry={best['entry_signal']}  drop={best['drop_level']}  "
            f"exit={best['exit_signal']}  buy={best['buy_pct']}  "
            f"base={best['alloc_base']}  x2={best['alloc_x2']}  "
            f"x3={best['alloc_x3']}",
            f"  CAGR {best['cagr']:.2f}%   worst year "
            f"{best['worst_ann_ret']:.2f}%   final "
            f"${port[-1]:,.0f}   ({base_tk} B&H CAGR {bh_cagr:.2f}%)",
            "",
            "  Year-by-year (best combo):",
        ]
        idx = df.index
        for yr in np.unique(idx.year):
            mask = np.where(idx.year == yr)[0]
            ann  = (port[mask[-1]] - port[mask[0]]) / port[mask[0]] * 100
            flag = "" if (yr < dd_start or ann >= -DD_LIMIT * 100) else "  ✗"
            best_block.append(f"    {yr}  start ${port[mask[0]]:>12,.0f}  "
                              f"end ${port[mask[-1]]:>12,.0f}  "
                              f"return {ann:+7.1f}%{flag}")
        print("\n".join(best_block))
        summary_lines += best_block

    (out_dir / f"{slug}_summary.txt").write_text(
        "\n".join(summary_lines), encoding="utf-8")
    print(f"\n  Saved: {out_dir / (slug + '_summary.txt')}")

    plot_top_combos(df, results, base_tk, args.exit_ma,
                    out_dir / f"{slug}_equity.png", args.no_show)
    plot_scatter(results, base_tk, args.exit_ma,
                 out_dir / f"{slug}_scatter.png", args.no_show)


if __name__ == "__main__":
    main()
