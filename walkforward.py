"""
walkforward.py  —  Expanding-window walk-forward backtester

Phase 1 (slow, cached):
  For each trade year Y in [start_year .. end_year]:
    Run the optimizer (grid search) ONCE on 2003-01-01 to (Y-1)-12-31.
    From that single passing set, pick a combo per selection rule in --select:
      cagr   — Highest CAGR (the best-performing passing combo).
      calmar — Balanced: highest Calmar = CAGR / |max drawdown|, i.e. the
               best return-per-drawdown trade-off.
    --select may list several rules (e.g. "cagr,calmar"); the grid is searched
    once and every rule derives its own schedule from it (same efficient
    pattern as daily_signal/reopt.py — N variants cost ~1× a grid search).
  Save each rule's schedule to results/walkforward/{preset}_param_schedule
  [_sel{rule}].json. Skip Phase 1 with --no-rebuild if the JSON already exists.

Phase 2 (fast):
  For each selection rule, run one continuous backtest from start_year to
  end_year. At Jan 1 of each year the strategy params are swapped to that
  year's pick — portfolio state (holdings, cash, armed) is preserved.

Usage:
  python walkforward.py --preset QQQ                       # Highest CAGR only
  python walkforward.py --preset QQQ --select cagr,calmar  # both variants, one grid pass
  python walkforward.py --preset SPY --exit-ma 100 --select calmar
  python walkforward.py --preset QQQ --no-rebuild --no-show
"""

import argparse
import json
import os
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

_no_show = "--no-show" in sys.argv
import matplotlib
if _no_show:
    matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent))
from backtester import run_backtest, print_results, cagr as compute_cagr, _tbill_daily

# Shared engine — the same data pipeline, backtest loop, DD filter and grid
# as the standalone optimizer.py, so Phase 1 here IS the optimizer run on a
# truncated training window.
from optimizer_core import (DD_LIMIT, DEFAULT_GRID, GRID_AXES, PRESETS,
                            build_grid, load_full_data, run_grid_search)


# ──────────────────────────────────────────────────────────────
# PHASE 1 — build param schedule
# ──────────────────────────────────────────────────────────────

def _rule_threshold(select: str, prefix: str):
    """Parse a '{prefix}{N}' rule (e.g. 'maxdd50' / 'buycap40'), returning N/100
    or None if `select` does not have that prefix."""
    if select.startswith(prefix):
        try:
            return int(select[len(prefix):]) / 100.0
        except ValueError:
            pass
    return None


def _maxdd_cap(select: str):
    return _rule_threshold(select, "maxdd")


def _buy_cap(select: str):
    return _rule_threshold(select, "buycap")


# The three pre-registered SPY-fix rules (SPY_FIX_PROTOCOL.md §3). `struct` caps
# both aggressiveness-monotone axes; `robust1`/`plateau` keep the premise floor
# drop >= 0 (never buy a rally day) and replace raw argmax with a noise-aware /
# robustness-first ranking.
STRUCT_BUY_CAP    = 0.40
STRUCT_DROP_FLOOR = 0.0025
PREMISE_DROP_FLOOR = 0.0
ROBUST_TOL_PP     = 1.0


def _plateau_scores(res) -> pd.Series:
    """Median CAGR of each combo's axis-aligned ±1-step grid neighborhood
    (self included). Neighbors are every *evaluated* combo — pass and fail
    alike — so a fragile spike next to filter-breaching neighbors scores low;
    pruned/absent grid points are simply not counted."""
    axes   = ["entry_signal", "drop_level", "exit_signal",
              "buy_pct", "alloc_base", "alloc_x2"]
    lookup = {}
    coords = np.empty((len(res), len(axes)), dtype=np.int64)
    for j, a in enumerate(axes):
        vals = np.sort(res[a].unique())
        vmap = {v: i for i, v in enumerate(vals)}
        coords[:, j] = res[a].map(vmap).to_numpy()
    cagr = res["cagr"].to_numpy()
    for c, g in zip(map(tuple, coords), cagr):
        lookup[c] = g
    scores = np.empty(len(res))
    for k in range(len(res)):
        c  = coords[k]
        vs = [cagr[k]]
        for j in range(len(axes)):
            for d in (-1, 1):
                cc    = c.copy()
                cc[j] += d
                v = lookup.get(tuple(cc))
                if v is not None:
                    vs.append(v)
        scores[k] = np.median(vs)
    return pd.Series(scores, index=res.index)


def _rank_combos(passing, select: str, res=None):
    """Rank one window's passing combos under a selection rule, best first.

    `_pick_combo` trades row 0; the fork-sensitivity diagnostic (protocol gate
    S2) trades rows 1..N-1. `res` (the full grid, pass+fail) is required only
    by 'plateau'. Ties resolve in grid order (stable sorts), matching idxmax.
    """
    if select == "struct":
        surv = passing[(passing["buy_pct"] <= STRUCT_BUY_CAP + 1e-9)
                       & (passing["drop_level"] >= STRUCT_DROP_FLOOR - 1e-9)]
        if surv.empty:
            surv = passing
        return surv.sort_values("cagr", ascending=False, kind="stable")

    if select == "robust1":
        surv = passing[passing["drop_level"] >= PREMISE_DROP_FLOOR - 1e-9]
        if surv.empty:
            surv = passing
        top  = surv["cagr"].max()
        tol  = surv[surv["cagr"] >= top - ROBUST_TOL_PP]
        tol  = tol.sort_values(["buy_pct", "drop_level", "exit_signal"],
                               ascending=[True, False, True], kind="stable")
        rest = surv.drop(tol.index).sort_values("cagr", ascending=False,
                                                kind="stable")
        return pd.concat([tol, rest])

    if select == "plateau":
        surv = passing[passing["drop_level"] >= PREMISE_DROP_FLOOR - 1e-9]
        if surv.empty:
            surv = passing
        scores = _plateau_scores(res if res is not None else passing)
        surv = surv.assign(_plateau=scores.reindex(surv.index))
        return (surv.sort_values(["_plateau", "cagr"], ascending=False,
                                 kind="stable")
                    .drop(columns="_plateau"))

    raise ValueError(f"_rank_combos: unknown ranked rule '{select}'")


def _pick_combo(passing, select: str, res=None):
    """Return (chosen_row, leader_row) for one training window's passing combos.

    select:
      'cagr'      — Highest CAGR: the best-performing passing combo. Maximizes
                    growth; converges to the most aggressive sizing.
      'maxdd{N}'  — highest-CAGR combo whose real-ETF-period max drawdown stays
                    within N% (e.g. 'maxdd50'). A mild in-sample regularizer:
                    excellent for QQQ (it even beats uncapped Highest-CAGR
                    out-of-sample), but it CANNOT bound a tail the training data
                    has never seen — on SPY the cap is slack on every pre-2022
                    window, so it does not prevent the 2022 loss.
      'buycap{N}' — Balanced (recommended): highest-CAGR combo with buy_pct ≤ N%
                    (e.g. 'buycap50'). A *structural* exposure cap (independent
                    of in-sample drawdown), so it limits leverage even against
                    an unseen tail. This is what lets SPY beat buy-and-hold
                    out-of-sample; for QQQ it costs almost nothing.
      'calmar'    — highest Calmar ratio (CAGR / |real-period maxDD|): the most
                    conservative rule, converges to 2× and the shallowest
                    drawdowns, at a real cost in CAGR.
      'struct'    — buy_pct ≤ 40% AND drop ≥ 0.25%, then top CAGR: both
                    aggressiveness-monotone axes structurally capped
                    (SPY_FIX_PROTOCOL.md candidate 1).
      'robust1'   — drop ≥ 0, then the most conservative combo within 1pp of
                    the top in-sample CAGR (candidate 2).
      'plateau'   — drop ≥ 0, ranked by ±1-step neighborhood median CAGR
                    (candidate 3). Needs `res`, the full pass+fail grid.
    leader is always the plain top-CAGR row, for reporting how much CAGR the
    chosen rule traded away.
    """
    leader   = passing.loc[passing["cagr"].idxmax()]
    maxdd    = _maxdd_cap(select)
    buy      = _buy_cap(select)
    if select in ("struct", "robust1", "plateau"):
        chosen = _rank_combos(passing, select, res).iloc[0]
    elif select == "calmar":
        chosen = passing.loc[passing["calmar"].idxmax()]
    elif maxdd is not None:
        survivors = passing[passing["max_dd_real"] >= -maxdd * 100]
        # If nothing survives the cap (very tight cap on an early window),
        # fall back to the shallowest-drawdown passing combo.
        chosen = (survivors.loc[survivors["cagr"].idxmax()] if not survivors.empty
                  else passing.loc[passing["max_dd_real"].idxmax()])
    elif buy is not None:
        survivors = passing[passing["buy_pct"] <= buy + 1e-9]
        chosen = (survivors.loc[survivors["cagr"].idxmax()] if not survivors.empty
                  else leader)
    else:
        chosen = leader
    return chosen, leader


def _row_to_params(chosen) -> dict:
    """Convert one passing-combo row into a schedule params dict."""
    return dict(
        entry_signal=chosen["entry_signal"], drop_level=chosen["drop_level"],
        exit_signal=chosen["exit_signal"],   buy_pct=chosen["buy_pct"],
        alloc_base=chosen["alloc_base"],     alloc_x2=chosen["alloc_x2"],
        alloc_x3=round(1 - chosen["alloc_x2"], 4),
        train_cagr=round(chosen["cagr"], 2),
        train_worst_year=round(chosen["worst_ann_ret"], 2),
        train_max_dd=round(chosen["max_dd"], 2),
        train_max_dd_real=round(chosen.get("max_dd_real", chosen["max_dd"]), 2),
        train_calmar=round(chosen["calmar"], 3),
    )


def build_param_schedules(preset: str, start_year: int, end_year: int,
                          df_full: pd.DataFrame, exit_ma: int = 200,
                          grid_version: str = DEFAULT_GRID,
                          workers: int = 1,
                          dd_limit: float = DD_LIMIT,
                          max_dd_limit: float = 1.0,
                          selects=("cagr", "calmar"),
                          save_grids: bool = True,
                          from_grids: bool = False) -> dict:
    """
    Build per-year param schedules for one or more selection rules in a SINGLE
    pass. The grid search (the expensive part) runs ONCE per training window;
    every rule in `selects` then picks its row from that same passing set —
    the same efficient pattern as daily_signal/reopt.py. Running the grid once
    and deriving N variants costs ~1× a grid search, not N×.

    selects : iterable of selection rules (see _pick_combo): 'cagr',
              'maxdd{N}' (e.g. 'maxdd50'), 'calmar' — or a single such string.
    Returns {select_rule: {trade_year: params_dict}}.

    save_grids : if True, write each training window's FULL grid result
                 (all combos, every metric) to results/walkforward/grids/ as a
                 gzip CSV (~1 MB/window). This lets you browse every year's
                 return/drawdown landscape and re-derive any selection rule
                 offline without re-running the (expensive) search.
    from_grids : if True, DON'T run the grid search — load each window's
                 previously-saved grid from results/walkforward/grids/ and just
                 re-derive the picks. Materializes any selection rule's full
                 schedule in seconds. Errors if a window's grid is missing.
    """
    if isinstance(selects, str):
        selects = (selects,)
    selects = list(dict.fromkeys(selects))  # de-dupe, preserve order

    dd_start  = PRESETS[preset]["dd_start"]
    grid      = build_grid(grid_version)
    schedules = {s: {} for s in selects}

    grids_dir = Path(__file__).parent / "results" / "walkforward" / "grids" / preset
    if save_grids:
        grids_dir.mkdir(parents=True, exist_ok=True)

    src = ("cached grids (optimizer re-run for any missing window)" if from_grids
           else f"{len(grid):,} combos searched ONCE per window")
    print(f"\nPhase 1 — building param schedule(s) ({preset}, "
          f"{start_year}–{end_year}, exit_ma=MA{exit_ma}, "
          f"{workers} worker(s), selects={','.join(selects)})")
    print(f"  {src} × {end_year - start_year + 1} training windows — "
          f"{len(selects)} rule(s) derived from each\n")

    for trade_yr in range(start_year, end_year + 1):
        train_end = trade_yr - 1
        gpath = grids_dir / (f"{preset}_ma{exit_ma}_train2003-{train_end}"
                             f"_results.csv.gz")

        if from_grids and gpath.exists():
            res = pd.read_csv(gpath)
        else:
            if from_grids:
                print(f"  {trade_yr}: no cached grid ({gpath.name}) — "
                      f"running optimizer for this window")
            if df_full is None:
                # --from-grids skips the upfront download; a missing window
                # still needs real data, so load it lazily (once).
                df_full = load_full_data(preset, f"{end_year}-12-31")
            df_train = df_full[df_full.index.year <= train_end]
            if len(df_train) < 250:
                print(f"  {trade_yr}: insufficient training data — skipped")
                continue
            res = run_grid_search(df_train, grid, exit_ma, dd_start,
                                  workers=workers, desc=f"  2003–{train_end}",
                                  dd_limit=dd_limit, max_dd_limit=max_dd_limit)
            if save_grids:
                res.to_csv(gpath, index=False, compression="gzip")

        passing = res[res["passed"]]
        if passing.empty:
            print(f"  {trade_yr}: no passing combo found")
            continue

        for s in selects:
            chosen, leader = _pick_combo(passing, s, res)
            schedules[s][trade_yr] = _row_to_params(chosen)
            tag = ""
            if chosen.name != leader.name:
                cagr_cost = leader["cagr"] - chosen["cagr"]
                dd_gain   = chosen["max_dd"] - leader["max_dd"]  # less neg = better
                tag = (f"  [-{cagr_cost:.2f}pp CAGR, +{dd_gain:.1f}pp maxDD "
                       f"vs top-CAGR]")
            p = schedules[s][trade_yr]
            print(f"  {trade_yr} [{s:6}]  entry={p['entry_signal']}  "
                  f"drop={p['drop_level']}  exit={p['exit_signal']}  "
                  f"buy={p['buy_pct']}  CAGR={p['train_cagr']:.1f}%  "
                  f"maxDD={p['train_max_dd']:.1f}%  "
                  f"Calmar={p['train_calmar']:.2f}{tag}")

    return schedules


def build_param_schedule(preset: str, start_year: int, end_year: int,
                         df_full: pd.DataFrame, exit_ma: int = 200,
                         grid_version: str = DEFAULT_GRID,
                         workers: int = 1,
                         dd_limit: float = DD_LIMIT,
                         select: str = "cagr") -> dict:
    """Single-rule convenience wrapper around build_param_schedules."""
    return build_param_schedules(
        preset, start_year, end_year, df_full, exit_ma=exit_ma,
        grid_version=grid_version,
        workers=workers, dd_limit=dd_limit, selects=(select,))[select]


# ──────────────────────────────────────────────────────────────
# PHASE 2 — walk-forward backtest
# ──────────────────────────────────────────────────────────────

def run_walkforward(preset: str, schedule: dict,
                    start_year: int, end_year: int,
                    capital: float, no_show: bool,
                    exit_ma: int = 200,
                    cash_yield: bool = False, grid_version: str = DEFAULT_GRID,
                    dd_limit: float = DD_LIMIT, select: str = "cagr",
                    max_dd_limit: float = 1.0):

    int_sched = {int(k): v for k, v in schedule.items()}
    # Fixed-model baseline = the params for the START year (trained on data
    # through start_year-1). Falls back to the earliest schedule row if the
    # start year isn't present.
    p0 = int_sched.get(start_year, int_sched[min(int_sched)])

    args = argparse.Namespace(
        preset=preset,
        start=f"{start_year}-01-01",
        end=f"{end_year}-12-31",
        capital=capital,
        entry_signal=p0["entry_signal"],
        exit_signal=p0["exit_signal"],
        drop_level=p0["drop_level"],
        buy_pct=p0["buy_pct"],
        alloc_base=p0["alloc_base"],
        alloc_x2=p0["alloc_x2"],
        alloc_x3=p0["alloc_x3"],
        exit_ma=exit_ma,
        cost_per_trade=0.0,
        cash_yield=cash_yield,
        no_show=no_show,
        save_plot=None,
    )

    print(f"\nPhase 2 — walk-forward backtest ({preset}, {start_year}–{end_year}, "
          f"exit_ma=MA{exit_ma}"
          f"{', cash yield ON' if cash_yield else ''})\n")
    hist, year_df, trans_df, base_tk = run_backtest(args, param_schedule=int_sched)
    print_results(hist, year_df, trans_df, base_tk, args)

    # Param schedule table
    W = 85
    print("\n" + "=" * W)
    print("  PARAM SCHEDULE — optimizer output per year (trained on all prior data)")
    print("=" * W)
    print(f"  {'Year':>4}  {'Entry':>6}  {'Drop%':>5}  {'Exit':>5}  "
          f"{'Buy%':>4}  {'Base%':>5}  {'X2%':>4}  {'Train CAGR':>11}")
    print("  " + "-" * 73)
    for yr in sorted(int_sched.keys()):
        p = int_sched[yr]
        print(f"  {yr}  {p['entry_signal']:>6.2f}  "
              f"{p['drop_level']*100:>5.2f}  "
              f"{p['exit_signal']:>5.2f}  "
              f"{p['buy_pct']*100:>4.0f}  "
              f"{p.get('alloc_base', 0)*100:>5.0f}  "
              f"{p.get('alloc_x2', 0)*100:>4.0f}  "
              f"{p.get('train_cagr', 0):>10.2f}%")

    out_dir = Path(__file__).parent / "results" / "walkforward"
    out_dir.mkdir(parents=True, exist_ok=True)

    # MA200 keeps original filenames for back-compat; non-200 gets a suffix.
    # The selection rule (_sel{rule}) keeps the Highest-CAGR and Balanced
    # variants on separate output files.
    ma_tag   = "" if exit_ma == 200 else f"_ma{exit_ma}"
    grid_tag = "" if grid_version == "v1" else f"_grid{grid_version}"
    dd_tag   = "" if dd_limit == DD_LIMIT else f"_dd{int(round(dd_limit*100))}"
    mdd_tag  = "" if max_dd_limit >= 1.0 else f"_maxdd{int(round(max_dd_limit*100))}"
    sel_tag  = "" if select == "cagr" else f"_sel{select}"
    cy_tag   = "_cy" if cash_yield else ""
    slug     = f"{preset}_walkforward_{start_year}-{end_year}{ma_tag}{grid_tag}{dd_tag}{mdd_tag}{sel_tag}{cy_tag}"
    year_df.to_csv(out_dir / f"{slug}_yearly.csv", index=False)
    print(f"\n  Saved: results/walkforward/{slug}_yearly.csv")

    _save_command_log(int_sched, preset, start_year, end_year,
                      out_dir / f"{slug}_commands.txt", exit_ma=exit_ma)

    # Fixed model: 2003-(start_year-1) params frozen for the full period
    print(f"\nRunning fixed model (2003-{start_year-1} params, frozen) for comparison …")
    hist_fixed, year_df_fixed = _run_fixed_model(
        preset, p0, start_year, end_year, capital, exit_ma=exit_ma,
        cash_yield=cash_yield)

    _print_comparison_table(year_df, year_df_fixed, hist, hist_fixed, base_tk,
                            start_year, end_year)

    _plot_comparison(hist, hist_fixed, base_tk, preset, start_year, end_year,
                     out_dir / f"{slug}_comparison.png", no_show,
                     exit_ma=exit_ma)

    return hist, year_df


def _save_command_log(int_sched: dict, preset: str, start_year: int,
                      end_year: int, save_path: Path, exit_ma: int = 200):
    import datetime
    ma_flag = "" if exit_ma == 200 else f" --exit-ma {exit_ma}"
    lines = [
        f"# Walk-forward command log — {preset} {start_year}–{end_year} "
        f"(exit MA{exit_ma})",
        f"# Generated: {datetime.date.today()}",
        f"#",
        f"# Each block is the equivalent backtester.py call for that trade year.",
        f"# Re-run any year with: python backtester.py [args]",
        f"",
    ]
    for yr in sorted(int_sched.keys()):
        p = int_sched[yr]
        lines += [
            f"# Year {yr}  (trained on 2003–{yr-1})",
            f"python backtester.py --preset {preset} --start {yr}-01-01 "
            f"--end {yr}-12-31{ma_flag} \\",
            f"  --entry-signal {p['entry_signal']} --drop-level {p['drop_level']} "
            f"--exit-signal {p['exit_signal']} \\",
            f"  --buy-pct {p['buy_pct']} --alloc-base {p.get('alloc_base', 0.0)} "
            f"--alloc-x2 {p.get('alloc_x2', 0.0)} --alloc-x3 {p.get('alloc_x3', 1.0)}",
            f"",
        ]
    save_path.write_text("\n".join(lines))
    print(f"  Saved: {save_path}")


def _run_fixed_model(preset: str, first_params: dict,
                     start_year: int, end_year: int, capital: float,
                     exit_ma: int = 200, cash_yield: bool = False):
    """Run backtester with 2003-(start_year-1) params frozen for the full period."""
    p = first_params
    args = argparse.Namespace(
        preset=preset,
        start=f"{start_year}-01-01",
        end=f"{end_year}-12-31",
        capital=capital,
        entry_signal=p["entry_signal"],
        exit_signal=p["exit_signal"],
        drop_level=p["drop_level"],
        buy_pct=p["buy_pct"],
        alloc_base=p["alloc_base"],
        alloc_x2=p["alloc_x2"],
        alloc_x3=p["alloc_x3"],
        exit_ma=exit_ma,
        cost_per_trade=0.0,
        cash_yield=cash_yield,
        no_show=True,
        save_plot=None,
    )
    hist, year_df, _, _ = run_backtest(args)
    return hist, year_df


def _print_comparison_table(year_exp, year_fix, hist_exp, hist_fix,
                             base_tk, start_year, end_year):
    W = 85
    print("\n" + "=" * W)
    print(f"  COMPARISON: Fixed (2003-{start_year-1}) vs Expanding Window vs {base_tk} B&H")
    print("=" * W)

    # Year-by-year table
    merged = year_exp[["Year", "Strategy Ret %", "Strategy Value"]].merge(
        year_fix[["Year", "Strategy Ret %", "Strategy Value"]],
        on="Year", suffixes=(" Exp", " Fix"),
    ).merge(
        year_exp[["Year", f"{base_tk} Ret %", f"{base_tk} Value"]], on="Year"
    )

    print(f"\n  {'Year':>4}  {'Fixed Ret%':>10}  {'Expand Ret%':>11}  "
          f"{'B&H Ret%':>9}  {'Fixed Val':>12}  {'Expand Val':>12}")
    print("  " + "-" * 72)
    for _, r in merged.iterrows():
        print(f"  {int(r['Year']):>4}  "
              f"{r['Strategy Ret % Fix']:>10.2f}%  "
              f"{r['Strategy Ret % Exp']:>10.2f}%  "
              f"{r[f'{base_tk} Ret %']:>8.2f}%  "
              f"${r['Strategy Value Fix']:>11,.0f}  "
              f"${r['Strategy Value Exp']:>11,.0f}")

    # Summary row
    days   = (hist_exp.index[-1] - hist_exp.index[0]).days
    cagr_e = compute_cagr(hist_exp["Strategy"].iloc[-1], hist_exp["Strategy"].iloc[0], days)
    cagr_f = compute_cagr(hist_fix["Strategy"].iloc[-1], hist_fix["Strategy"].iloc[0], days)
    cagr_b = compute_cagr(hist_exp["BuyHold"].iloc[-1],  hist_exp["BuyHold"].iloc[0],  days)

    worst_e = year_exp["Strategy Ret %"].min()
    worst_f = year_fix["Strategy Ret %"].min()
    worst_b = year_exp[f"{base_tk} Ret %"].min()

    worst_yr_e = int(year_exp.loc[year_exp["Strategy Ret %"].idxmin(), "Year"])
    worst_yr_f = int(year_fix.loc[year_fix["Strategy Ret %"].idxmin(), "Year"])
    worst_yr_b = int(year_exp.loc[year_exp[f"{base_tk} Ret %"].idxmin(), "Year"])

    print("\n  " + "-" * 72)
    print(f"  {'CAGR':>18}  {cagr_f*100:>10.2f}%  {cagr_e*100:>10.2f}%  {cagr_b*100:>8.2f}%")
    print(f"  {'Final Value':>18}  "
          f"${hist_fix['Strategy'].iloc[-1]:>11,.0f}  "
          f"${hist_exp['Strategy'].iloc[-1]:>11,.0f}  "
          f"${hist_exp['BuyHold'].iloc[-1]:>8,.0f}")
    print(f"  {'Worst Year':>18}  "
          f"{worst_f:>9.1f}% ({worst_yr_f})  "
          f"{worst_e:>9.1f}% ({worst_yr_e})  "
          f"{worst_b:>7.1f}% ({worst_yr_b})")
    green_e = (merged["Strategy Ret % Exp"] > 0).sum()
    green_f = (merged["Strategy Ret % Fix"] > 0).sum()
    green_b = (merged[f"{base_tk} Ret %"] > 0).sum()
    total   = len(merged)
    out_e   = (merged["Strategy Ret % Exp"] > merged[f"{base_tk} Ret %"]).sum()
    out_f   = (merged["Strategy Ret % Fix"] > merged[f"{base_tk} Ret %"]).sum()
    def _sharpe(hist_col):
        r  = hist_col.pct_change().dropna()
        rf = _tbill_daily(r.index)
        ex = r - rf
        return ex.mean() / ex.std() * np.sqrt(252) if ex.std() > 0 else 0.0

    sharpe_e = _sharpe(hist_exp["Strategy"])
    sharpe_f = _sharpe(hist_fix["Strategy"])
    sharpe_b = _sharpe(hist_exp["BuyHold"])

    print(f"  {'Edge vs B&H':>18}  "
          f"{(cagr_f-cagr_b)*100:>+9.2f}pp  "
          f"{(cagr_e-cagr_b)*100:>+10.2f}pp  "
          f"{'—':>9}")
    print(f"  {'Sharpe ratio':>18}  "
          f"{sharpe_f:>12.2f}  "
          f"{sharpe_e:>12.2f}  "
          f"{sharpe_b:>9.2f}")
    print(f"  {'Green years':>18}  "
          f"{green_f}/{total} ({green_f/total*100:.0f}%)   "
          f"{green_e}/{total} ({green_e/total*100:.0f}%)   "
          f"{green_b}/{total} ({green_b/total*100:.0f}%)")
    print(f"  {'Beat B&H years':>18}  "
          f"{out_f}/{total} ({out_f/total*100:.0f}%)   "
          f"{out_e}/{total} ({out_e/total*100:.0f}%)   "
          f"{'—':>12}")


def _plot_comparison(hist_exp, hist_fix, base_tk, preset,
                     start_year, end_year, save_path, no_show,
                     exit_ma: int = 200):
    fig, ax = plt.subplots(figsize=(14, 7))

    days   = (hist_exp.index[-1] - hist_exp.index[0]).days
    cagr_e = compute_cagr(hist_exp["Strategy"].iloc[-1], hist_exp["Strategy"].iloc[0], days)
    cagr_f = compute_cagr(hist_fix["Strategy"].iloc[-1], hist_fix["Strategy"].iloc[0], days)
    cagr_b = compute_cagr(hist_exp["BuyHold"].iloc[-1],  hist_exp["BuyHold"].iloc[0],  days)

    ax.plot(hist_exp.index, hist_exp["BuyHold"],
            label=f"{base_tk} Buy & Hold  ({cagr_b*100:.2f}% CAGR)",
            linewidth=1.5, color="steelblue")
    ax.plot(hist_fix.index, hist_fix["Strategy"],
            label=f"Fixed model 2003-{start_year-1}  ({cagr_f*100:.2f}% CAGR)",
            linewidth=1.5, color="mediumseagreen", linestyle="--")
    ax.plot(hist_exp.index, hist_exp["Strategy"],
            label=f"Expanding window  ({cagr_e*100:.2f}% CAGR)",
            linewidth=1.5, color="darkorange")

    ax.set_title(
        f"Walk-Forward Comparison — {preset}  |  {start_year}–{end_year}  "
        f"|  exit MA{exit_ma}\n"
        f"Fixed (2003-{start_year-1} params, frozen)  vs  "
        f"Expanding window (re-optimized each year)  vs  {base_tk} B&H",
        fontsize=10,
    )
    ax.set_xlabel("Date")
    ax.set_ylabel("Portfolio Value ($)")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.4)
    plt.tight_layout()
    plt.savefig(save_path, dpi=150)
    print(f"  Saved: {save_path}")
    if not no_show:
        plt.show(block=True)
    plt.close()

    # --- drawdown figure ---
    bh_dd    = (hist_exp["BuyHold"]  / hist_exp["BuyHold"].cummax()  - 1) * 100
    fix_dd   = (hist_fix["Strategy"] / hist_fix["Strategy"].cummax() - 1) * 100
    exp_dd   = (hist_exp["Strategy"] / hist_exp["Strategy"].cummax() - 1) * 100

    fig2, ax2 = plt.subplots(figsize=(14, 5))
    ax2.plot(hist_exp.index, bh_dd,  color="steelblue",      linewidth=1.2,
             label=f"{base_tk} B&H")
    ax2.plot(hist_fix.index, fix_dd, color="mediumseagreen",  linewidth=1.2,
             linestyle="--", label=f"Fixed model 2003-{start_year-1}")
    ax2.plot(hist_exp.index, exp_dd, color="darkorange",      linewidth=1.2,
             label="Expanding window")
    ax2.fill_between(hist_exp.index, exp_dd, 0, alpha=0.12, color="darkorange")
    ax2.set_title(
        f"Drawdown — Walk-Forward {preset}  |  {start_year}–{end_year}  "
        f"|  exit MA{exit_ma}",
        fontsize=10,
    )
    ax2.set_ylabel("Drawdown (%)")
    ax2.set_xlabel("Date")
    ax2.legend(fontsize=11)
    ax2.grid(True, alpha=0.4)
    ax2.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    plt.tight_layout()

    p = Path(save_path)
    dd_path = p.with_name(p.stem + "_drawdown" + p.suffix)
    plt.savefig(dd_path, dpi=150)
    print(f"  Saved: {dd_path}")
    if not no_show:
        plt.show(block=True)
    plt.close()


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(
        description="Expanding-window walk-forward backtester",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--preset",     default="QQQ", choices=["QQQ", "SPY", "IWM"])
    p.add_argument("--start-year", type=int, default=2015)
    p.add_argument("--end-year",   type=int, default=2026)
    p.add_argument("--capital",    type=float, default=10_000)
    p.add_argument("--exit-ma",    type=int, default=200, choices=[50, 100, 200],
                   help="MA period used for exit signal "
                        "(arm/entry always uses MA200)")
    p.add_argument("--workers", type=int,
                   default=max(1, (os.cpu_count() or 4) - 2),
                   help="Parallel worker processes for the Phase 1 grid search. "
                        "Results are identical to a single-worker run.")
    p.add_argument("--grid", default=DEFAULT_GRID, choices=list(GRID_AXES),
                   help="Optimizer grid (defined in optimizer_core). The "
                        "production grid is the default; alternates are kept "
                        "only for reproducing historical studies.")
    p.add_argument("--select", default="cagr,maxdd50,buycap50",
                   help="Per-window selection rule(s) among DD-filter survivors, "
                        "comma-separated for several in one grid pass. "
                        "'cagr' = Highest CAGR (max growth). 'maxdd{N}' = highest "
                        "CAGR with real-period maxDD within N%% (best for QQQ). "
                        "'buycap{N}' = highest CAGR with buy_pct <= N%% — a "
                        "structural exposure cap (the robust cross-index "
                        "Balanced rule; the only one that lifts SPY past B&H "
                        "out-of-sample). 'calmar' = highest CAGR/|maxDD| (most "
                        "conservative, ~2x). Pre-registered SPY-fix rules "
                        "(SPY_FIX_PROTOCOL.md): 'struct' (buy<=40%% and "
                        "drop>=0.25%%), 'robust1' (most conservative within 1pp "
                        "of top CAGR, drop>=0), 'plateau' (neighborhood-median "
                        "CAGR rank, drop>=0). Non-'cagr' rules get a _sel{rule} "
                        "output suffix.")
    p.add_argument("--no-save-grids", action="store_true",
                   help="Do not save each training window's full grid result "
                        "(by default Phase 1 writes results/walkforward/grids/"
                        "{preset}/*.csv.gz, ~1 MB/window, so every year's full "
                        "return/drawdown landscape can be browsed and any "
                        "selection rule re-derived offline).")
    p.add_argument("--from-grids", action="store_true",
                   help="Re-derive the schedule(s) from the per-window grids "
                        "saved earlier in results/walkforward/grids/ instead of "
                        "searching. Materializes any --select rule's full "
                        "walk-forward (schedule + charts + CSVs) in seconds. Any "
                        "window whose grid is missing falls back to running the "
                        "optimizer for that window (and caches it unless "
                        "--no-save-grids).")
    p.add_argument("--max-dd", type=float, default=1.0,
                   help="Hard max-drawdown ceiling for the Phase 1 filter "
                        "(fraction, e.g. 0.50 rejects any combo whose real-period "
                        "peak-to-trough drawdown exceeds 50%%). Default 1.0 = no "
                        "cap. Directly bounds the worst-case tail; gets a "
                        "_maxdd{N} output suffix.")
    p.add_argument("--dd-limit", type=float, default=DD_LIMIT,
                   help="Calendar-year loss cap for the Phase 1 pass/fail filter "
                        "(fraction, e.g. 0.30 = combos that lost >30%% in any "
                        "in-sample year from the ETF-inception cutoff are "
                        "rejected). Default 0.40. Non-default values get a "
                        "_dd{N} output suffix.")
    p.add_argument("--cash-yield", action="store_true",
                   help="Accrue daily T-bill interest (^IRX) on idle cash in the "
                        "Phase 2 backtest (models SGOV/BIL). Phase 1 optimizer "
                        "rankings are unaffected.")
    p.add_argument("--no-rebuild", action="store_true",
                   help="Skip Phase 1 if the param schedule JSON already exists")
    p.add_argument("--only-year",  type=int, default=None,
                   help="Optimize only this single trade year (training data = "
                        "2003 to Dec 31 of year-1) and MERGE the result into the "
                        "existing schedule JSON, preserving all other years. "
                        "Skips Phase 2 (backtest, plots, CSVs). Use this for the "
                        "annual January re-opt — much faster than rebuilding the "
                        "whole schedule.")
    p.add_argument("--no-show",    action="store_true",
                   help="Suppress interactive plot window")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    # --only-year: scope the run to a single window, skip Phase 2
    if args.only_year is not None:
        args.start_year = args.only_year
        args.end_year   = args.only_year

    out_dir = Path(__file__).parent / "results" / "walkforward"
    out_dir.mkdir(parents=True, exist_ok=True)

    # --select may be comma-separated (e.g. "cagr,calmar"): the grid is
    # searched once per window and each rule derives its own schedule.
    selects = [s.strip() for s in args.select.split(",") if s.strip()]
    bad = [s for s in selects
           if s not in ("cagr", "calmar", "struct", "robust1", "plateau")
           and _maxdd_cap(s) is None and _buy_cap(s) is None]
    if bad or not selects:
        sys.exit(f"--select: invalid rule(s) {bad or '(empty)'}; choose from "
                 f"'cagr', 'calmar', 'maxdd{{N}}', 'buycap{{N}}' "
                 f"(e.g. maxdd50, buycap50), 'struct', 'robust1', 'plateau', "
                 f"comma-separated.")

    # Per-rule schedule path. MA200 keeps the original filename for back-compat;
    # non-200 adds _ma{N}; grid / dd-limit / select each add a tag so variants
    # never collide on the same file.
    def _sched_path(select: str) -> Path:
        suffix = "" if args.exit_ma == 200 else f"_ma{args.exit_ma}"
        if args.grid != "v1":
            suffix += f"_grid{args.grid}"
        if args.dd_limit != DD_LIMIT:
            suffix += f"_dd{int(round(args.dd_limit*100))}"
        if args.max_dd < 1.0:
            suffix += f"_maxdd{int(round(args.max_dd*100))}"
        if select != "cagr":
            suffix += f"_sel{select}"
        return out_dir / f"{args.preset}_param_schedule{suffix}.json"

    # Build (or load) one schedule per selection rule.
    schedules: dict[str, dict] = {}
    to_build = [s for s in selects
                if not (args.no_rebuild and _sched_path(s).exists())]
    for s in selects:
        if args.no_rebuild and _sched_path(s).exists():
            print(f"Loading cached schedule [{s}]: {_sched_path(s)}")
            schedules[s] = json.loads(_sched_path(s).read_text())

    if to_build:
        if args.from_grids:
            df_full = None  # grids are cached — no data download / search needed
        else:
            # For --only-year, just need data through that year's training cutoff.
            end_buffer = (f"{args.only_year}-01-15"
                          if args.only_year is not None
                          else f"{args.end_year}-12-31")
            df_full = load_full_data(args.preset, end_buffer)
        built = build_param_schedules(
            args.preset, args.start_year, args.end_year, df_full,
            exit_ma=args.exit_ma,
            grid_version=args.grid, workers=args.workers,
            dd_limit=args.dd_limit, max_dd_limit=args.max_dd, selects=to_build,
            save_grids=not args.no_save_grids, from_grids=args.from_grids)

        for s, schedule in built.items():
            path = _sched_path(s)
            if args.only_year is not None:
                # Merge into existing schedule — preserve all other years.
                existing = json.loads(path.read_text()) if path.exists() else {}
                for yr_int, row in schedule.items():
                    existing[str(yr_int)] = row
                path.write_text(json.dumps(existing, indent=2))
                years_now = sorted(int(k) for k in existing.keys())
                print(f"\n  [{s}] Merged year {args.only_year} into {path}")
                print(f"  [{s}] Schedule now spans: {years_now[0]}–"
                      f"{years_now[-1]} ({len(years_now)} rows)")
            else:
                path.write_text(json.dumps(schedule, indent=2))
                print(f"\n  Saved schedule [{s}]: {path}")
            schedules[s] = schedule

    # --only-year: skip Phase 2 — there's no meaningful continuous backtest window
    if args.only_year is not None:
        print(f"  Phase 2 skipped (--only-year mode).")
        sys.exit(0)

    # Phase 2 — one walk-forward backtest per selection rule.
    for s in selects:
        run_walkforward(
            args.preset, schedules[s],
            args.start_year, args.end_year,
            args.capital, args.no_show,
            exit_ma=args.exit_ma,
            cash_yield=args.cash_yield,
            grid_version=args.grid,
            dd_limit=args.dd_limit,
            select=s,
            max_dd_limit=args.max_dd,
        )
