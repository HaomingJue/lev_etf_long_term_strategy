"""fork_sensitivity.py — protocol gate S2 (SPY_FIX_PROTOCOL.md §4).

A walk-forward headline is only trustworthy if it doesn't hinge on which of
several near-equivalent in-sample picks the optimizer happened to return
(the 2023 SPY MA200 fork: two combos 0.7pp apart in-sample landed +42% vs
+0.4% out-of-sample). This script rebuilds the entire walk-forward schedule
from the rule's rank-R pick in EVERY window, for R = 1..N, and backtests each
schedule. Gate S2: the rank-2 schedule must still beat B&H, and the rank-1..5
CAGR band must be narrower than 8pp.

Usage:
  python experiments/fork_sensitivity.py --preset SPY --exit-ma 200 --select struct
Reads the cached per-window grids (results/walkforward/grids/); prints a table,
writes nothing.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
if "--no-show" not in sys.argv:            # keep walkforward's matplotlib on Agg
    sys.argv.append("--no-show")
from backtester import run_backtest, cagr as compute_cagr
from walkforward import _rank_combos, _row_to_params


def build_rank_schedule(preset: str, exit_ma: int, select: str, rank: int,
                        start_year: int, end_year: int) -> dict:
    gdir = (Path(__file__).parent.parent / "results" / "walkforward"
            / "grids" / preset)
    schedule = {}
    for trade_yr in range(start_year, end_year + 1):
        gpath = gdir / (f"{preset}_ma{exit_ma}_train2003-{trade_yr - 1}"
                        f"_results.csv.gz")
        if not gpath.exists():
            sys.exit(f"missing cached grid: {gpath} — run walkforward.py "
                     f"(without --no-save-grids) for this window first")
        res     = pd.read_csv(gpath)
        ranked  = _rank_combos(res[res["passed"]], select, res)
        row     = ranked.iloc[min(rank - 1, len(ranked) - 1)]
        schedule[trade_yr] = _row_to_params(row)
    return schedule


def run_schedule(preset: str, schedule: dict, exit_ma: int,
                 start_year: int, end_year: int):
    args = argparse.Namespace(
        preset=preset, start=f"{start_year}-01-01", end=f"{end_year}-12-31",
        capital=10_000,
        entry_signal=schedule[start_year]["entry_signal"],
        exit_signal=schedule[start_year]["exit_signal"],
        drop_level=schedule[start_year]["drop_level"],
        buy_pct=schedule[start_year]["buy_pct"],
        alloc_base=schedule[start_year]["alloc_base"],
        alloc_x2=schedule[start_year]["alloc_x2"],
        alloc_x3=schedule[start_year]["alloc_x3"],
        exit_ma=exit_ma, cost_per_trade=0.0, cash_yield=False,
        no_show=True, save_plot=None,
    )
    hist, year_df, _, _ = run_backtest(args, param_schedule=schedule)
    days = (hist.index[-1] - hist.index[0]).days
    c    = compute_cagr(hist["Strategy"].iloc[-1], hist["Strategy"].iloc[0], days)
    b    = compute_cagr(hist["BuyHold"].iloc[-1],  hist["BuyHold"].iloc[0],  days)
    return c * 100, b * 100, year_df["Strategy Ret %"].min()


def main():
    p = argparse.ArgumentParser(description="Fork-sensitivity gate (S2)")
    p.add_argument("--preset",     default="SPY", choices=["QQQ", "SPY", "IWM"])
    p.add_argument("--exit-ma",    type=int, default=200, choices=[50, 100, 200])
    p.add_argument("--select",     default="struct",
                   choices=["struct", "robust1", "plateau"])
    p.add_argument("--ranks",      type=int, default=5)
    p.add_argument("--start-year", type=int, default=2015)
    p.add_argument("--end-year",   type=int, default=2026)
    p.add_argument("--no-show",    action="store_true")
    args = p.parse_args()

    print(f"\nFork sensitivity — {args.preset} MA{args.exit_ma} "
          f"[{args.select}], ranks 1–{args.ranks}, "
          f"{args.start_year}–{args.end_year}\n")

    rows, bh = [], None
    for r in range(1, args.ranks + 1):
        sched = build_rank_schedule(args.preset, args.exit_ma, args.select, r,
                                    args.start_year, args.end_year)
        c, bh, worst = run_schedule(args.preset, sched, args.exit_ma,
                                    args.start_year, args.end_year)
        rows.append((r, c, worst))
        print(f"  rank {r}:  CAGR {c:6.2f}%   worst year {worst:7.2f}%")

    band = max(c for _, c, _ in rows) - min(c for _, c, _ in rows)
    print(f"\n  B&H CAGR      : {bh:.2f}%")
    print(f"  rank-2 vs B&H : {rows[1][1] - bh:+.2f}pp  "
          f"({'PASS' if rows[1][1] >= bh else 'FAIL'} S2a)")
    print(f"  rank-1..{args.ranks} band: {band:.2f}pp  "
          f"({'PASS' if band < 8.0 else 'FAIL'} S2b)")


if __name__ == "__main__":
    main()
