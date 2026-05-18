"""
walkforward.py  —  Expanding-window walk-forward backtester

Phase 1 (slow, cached):
  For each trade year Y in [start_year .. end_year]:
    Run the optimizer on 2003-01-01 to (Y-1)-12-31.
    Pick the best passing combo by CAGR.
  Save the param schedule to results/walkforward/{preset}_param_schedule.json.
  Skip Phase 1 with --no-rebuild if the JSON already exists.

Phase 2 (fast):
  Run one continuous backtest from start_year to end_year.
  At Jan 1 of each year the strategy params are swapped to that year's
  optimizer output — portfolio state (holdings, cash, armed) is preserved.

Usage:
  python walkforward.py --preset QQQ
  python walkforward.py --preset SPY --start-year 2014 --end-year 2025
  python walkforward.py --preset QQQ --no-rebuild --no-show
"""

import argparse
import itertools
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from tqdm import tqdm

_no_show = "--no-show" in sys.argv
import matplotlib
if _no_show:
    matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent))
from backtester import run_backtest, print_results, cagr as compute_cagr


# ──────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────

PRESETS = {
    "QQQ": {"base": "QQQ", "lev2": "QLD",  "lev3": "TQQQ", "dd_start": 2010},
    "SPY": {"base": "SPY", "lev2": "SSO",  "lev3": "UPRO", "dd_start": 2009},
    "IWM": {"base": "IWM", "lev2": "UWM",  "lev3": "TNA",  "dd_start": 2009},
}

START_DATA    = "2003-01-01"
CAPITAL       = 10_000
DD_LIMIT      = 0.40

ENTRY_SIGNALS = [1.01, 1.02, 1.03, 1.04, 1.05, 1.06]
DROP_LEVELS   = [0.005, 0.010, 0.015, 0.020, 0.025, 0.030]
EXIT_SIGNALS  = [0.95, 0.97, 0.99, 1.00, 1.01, 1.02]
BUY_PCTS      = [0.10, 0.20, 0.30, 0.40]
ALLOC_BASES   = [0.0, 0.10, 0.20, 0.30]
ALLOC_X2S     = [0.0, 0.25, 0.50, 0.75, 1.0]


# ──────────────────────────────────────────────────────────────
# DATA
# ──────────────────────────────────────────────────────────────

def _build_lev_nav(base: pd.Series, real: pd.Series, L: int) -> pd.Series:
    ret   = base.pct_change().fillna(0)
    var20 = ret.rolling(20).var().fillna(0)
    nav   = np.ones(len(base))
    for i in range(1, len(base)):
        nav[i] = nav[i-1] * (1.0 + L * ret.values[i]
                             - 0.5 * (L**2 - L) * var20.values[i])
    synth = pd.Series(nav, index=base.index)
    if real is None or real.dropna().empty:
        return synth
    common = base.index.intersection(real.dropna().index)
    if common.empty:
        return synth
    first    = common[0]
    stitched = synth.copy()
    stitched.loc[first:] = (real.reindex(base.index).loc[first:]
                            * (synth.loc[first] / real.loc[first]))
    return stitched


def load_full_data(preset: str, end: str) -> pd.DataFrame:
    cfg = PRESETS[preset]
    base_tk, lev2_tk, lev3_tk = cfg["base"], cfg["lev2"], cfg["lev3"]
    print(f"Downloading {base_tk}, {lev2_tk}, {lev3_tk} …")

    def dl(tk):
        try:
            s = yf.download(tk, start=START_DATA, end=end,
                            auto_adjust=True, progress=False)["Close"].squeeze().dropna()
            s.name = tk
            return s
        except Exception:
            return pd.Series(dtype=float)

    base = dl(base_tk)
    df   = pd.DataFrame({
        "base": base,
        "lev2": _build_lev_nav(base, dl(lev2_tk), 2),
        "lev3": _build_lev_nav(base, dl(lev3_tk), 3),
    }).dropna(subset=["base"])
    df["ret"]   = df["base"].pct_change().fillna(0)
    df["MA200"] = df["base"].rolling(200).mean()
    return df


# ──────────────────────────────────────────────────────────────
# OPTIMIZER  (self-contained, works for any preset)
# ──────────────────────────────────────────────────────────────

def _build_grid():
    return [(e, d, x, b, ab, ax2)
            for e, d, x, b, ab, ax2 in itertools.product(
                ENTRY_SIGNALS, DROP_LEVELS, EXIT_SIGNALS,
                BUY_PCTS, ALLOC_BASES, ALLOC_X2S)
            if x < e]


def _opt_backtest(df: pd.DataFrame, entry, drop, exit_, buy, ab, ax2):
    ax3  = 1.0 - ax2
    f    = df.iloc[0]
    nb   = df["base"].values / f["base"]
    n2   = df["lev2"].values / f["lev2"]
    n3   = df["lev3"].values / f["lev3"]
    ma   = df["MA200"].values / f["base"]

    cash = CAPITAL
    s_b = s_2 = s_3 = 0.0
    armed = bf = bt = False
    port  = np.empty(len(df))
    port[0] = CAPITAL

    for i in range(1, len(df)):
        if np.isnan(ma[i]) or ma[i] == 0:
            port[i] = port[i-1]
            continue

        vb = s_b * nb[i]; v2 = s_2 * n2[i]; v3 = s_3 * n3[i]
        tot = cash + vb + v2 + v3

        if nb[i] < ma[i] * exit_ and (s_2 > 0 or s_3 > 0):
            cash += v2 + v3
            s_2 = s_3 = 0.0
            if not bt and ab > 0:
                vb  = s_b * nb[i]; tot = cash + vb; tgt = tot * ab
                if vb > tgt + 0.01:
                    s_b -= (vb - tgt) / nb[i]; cash += vb - tgt
                bt = True
            armed = False
        else:
            if not armed and nb[i] > ma[i] * entry:
                armed = True
            d = (nb[i-1] - nb[i]) / nb[i-1] if nb[i-1] > 0 else 0.0
            if armed and nb[i] > ma[i] * entry and d >= drop and cash > 0.01:
                tot = cash + s_b * nb[i] + s_2 * n2[i] + s_3 * n3[i]
                if not bf and ab > 0:
                    sp = min(max(tot * ab - s_b * nb[i], 0), cash)
                    if sp > 0.01:
                        s_b += sp / nb[i]; cash -= sp
                    bf = True
                    tot = cash + s_b * nb[i] + s_2 * n2[i] + s_3 * n3[i]
                lev = min(buy * tot, cash)
                if lev > 0.01:
                    if ax2 > 0: s_2 += lev * ax2 / n2[i]
                    if ax3 > 0: s_3 += lev * ax3 / n3[i]
                    cash -= lev

        port[i] = cash + s_b * nb[i] + s_2 * n2[i] + s_3 * n3[i]

    days = (df.index[-1] - df.index[0]).days
    c = (port[-1] / CAPITAL) ** (365.25 / days) - 1 if days > 0 else 0.0
    return c, port


def _check_dd(df: pd.DataFrame, port: np.ndarray, dd_start: int):
    idx   = df.index
    worst = 0.0
    for yr in np.unique(idx.year):
        mask = np.where(idx.year == yr)[0]
        ann  = (port[mask[-1]] - port[mask[0]]) / port[mask[0]]
        worst = min(worst, ann)
        if yr >= dd_start and ann < -DD_LIMIT:
            return False, ann
    return True, worst


# ──────────────────────────────────────────────────────────────
# PHASE 1 — build param schedule
# ──────────────────────────────────────────────────────────────

def build_param_schedule(preset: str, start_year: int, end_year: int,
                         df_full: pd.DataFrame) -> dict:
    dd_start = PRESETS[preset]["dd_start"]
    grid     = _build_grid()
    schedule = {}

    print(f"\nPhase 1 — building param schedule ({preset}, "
          f"{start_year}–{end_year})")
    print(f"  {len(grid):,} combos × {end_year - start_year + 1} training windows\n")

    for trade_yr in range(start_year, end_year + 1):
        train_end = trade_yr - 1
        df_train  = df_full[df_full.index.year <= train_end]
        if len(df_train) < 250:
            print(f"  {trade_yr}: insufficient training data — skipped")
            continue

        best_cagr, best = -np.inf, None
        for entry, drop, exit_, buy, ab, ax2 in tqdm(
                grid, desc=f"  2003–{train_end}", leave=False):
            c, port = _opt_backtest(df_train, entry, drop, exit_, buy, ab, ax2)
            ok, _   = _check_dd(df_train, port, dd_start)
            if ok and c > best_cagr:
                best_cagr = c
                best = dict(
                    entry_signal=entry, drop_level=drop,
                    exit_signal=exit_,  buy_pct=buy,
                    alloc_base=ab,      alloc_x2=ax2,
                    alloc_x3=round(1 - ax2, 4),
                    train_cagr=round(c * 100, 2),
                )

        if best:
            schedule[trade_yr] = best
            print(f"  {trade_yr}  train=2003–{train_end}  "
                  f"entry={best['entry_signal']}  drop={best['drop_level']}  "
                  f"exit={best['exit_signal']}  buy={best['buy_pct']}  "
                  f"CAGR(train)={best['train_cagr']:.1f}%")
        else:
            print(f"  {trade_yr}: no passing combo found")

    return schedule


# ──────────────────────────────────────────────────────────────
# PHASE 2 — walk-forward backtest
# ──────────────────────────────────────────────────────────────

def run_walkforward(preset: str, schedule: dict,
                    start_year: int, end_year: int,
                    capital: float, no_show: bool):

    int_sched = {int(k): v for k, v in schedule.items()}
    p0        = int_sched[min(int_sched)]

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
        exit_ma=200,
        no_show=no_show,
        save_plot=None,
    )

    print(f"\nPhase 2 — walk-forward backtest ({preset}, {start_year}–{end_year})\n")
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

    slug = f"{preset}_walkforward_{start_year}-{end_year}"
    year_df.to_csv(out_dir / f"{slug}_yearly.csv", index=False)
    print(f"\n  Saved: results/walkforward/{slug}_yearly.csv")

    _save_command_log(int_sched, preset, start_year, end_year,
                      out_dir / f"{slug}_commands.txt")

    # Fixed model: 2003-(start_year-1) params frozen for the full period
    print(f"\nRunning fixed model (2003-{start_year-1} params, frozen) for comparison …")
    hist_fixed, year_df_fixed = _run_fixed_model(
        preset, p0, start_year, end_year, capital)

    _print_comparison_table(year_df, year_df_fixed, hist, hist_fixed, base_tk,
                            start_year, end_year)

    _plot_comparison(hist, hist_fixed, base_tk, preset, start_year, end_year,
                     out_dir / f"{slug}_comparison.png", no_show)

    return hist, year_df


def _save_command_log(int_sched: dict, preset: str, start_year: int,
                      end_year: int, save_path: Path):
    import datetime
    lines = [
        f"# Walk-forward command log — {preset} {start_year}–{end_year}",
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
            f"python backtester.py --preset {preset} --start {yr}-01-01 --end {yr}-12-31 \\",
            f"  --entry-signal {p['entry_signal']} --drop-level {p['drop_level']} "
            f"--exit-signal {p['exit_signal']} \\",
            f"  --buy-pct {p['buy_pct']} --alloc-base {p.get('alloc_base', 0.0)} "
            f"--alloc-x2 {p.get('alloc_x2', 0.0)} --alloc-x3 {p.get('alloc_x3', 1.0)}",
            f"",
        ]
    save_path.write_text("\n".join(lines))
    print(f"  Saved: {save_path}")


def _run_fixed_model(preset: str, first_params: dict,
                     start_year: int, end_year: int, capital: float):
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
        exit_ma=200,
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
    print(f"  {'Edge vs B&H':>18}  "
          f"{(cagr_f-cagr_b)*100:>+9.2f}pp  "
          f"{(cagr_e-cagr_b)*100:>+10.2f}pp  "
          f"{'—':>9}")


def _plot_comparison(hist_exp, hist_fix, base_tk, preset,
                     start_year, end_year, save_path, no_show):
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
        f"Walk-Forward Comparison — {preset}  |  {start_year}–{end_year}\n"
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


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(
        description="Expanding-window walk-forward backtester",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--preset",     default="QQQ", choices=["QQQ", "SPY", "IWM"])
    p.add_argument("--start-year", type=int, default=2014)
    p.add_argument("--end-year",   type=int, default=2025)
    p.add_argument("--capital",    type=float, default=10_000)
    p.add_argument("--no-rebuild", action="store_true",
                   help="Skip Phase 1 if the param schedule JSON already exists")
    p.add_argument("--no-show",    action="store_true",
                   help="Suppress interactive plot window")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    out_dir       = Path(__file__).parent / "results" / "walkforward"
    out_dir.mkdir(parents=True, exist_ok=True)
    schedule_path = out_dir / f"{args.preset}_param_schedule.json"

    if args.no_rebuild and schedule_path.exists():
        print(f"Loading cached schedule: {schedule_path}")
        schedule = json.loads(schedule_path.read_text())
    else:
        df_full  = load_full_data(args.preset, f"{args.end_year}-12-31")
        schedule = build_param_schedule(
            args.preset, args.start_year, args.end_year, df_full)
        schedule_path.write_text(json.dumps(schedule, indent=2))
        print(f"\n  Saved schedule: {schedule_path}")

    run_walkforward(
        args.preset, schedule,
        args.start_year, args.end_year,
        args.capital, args.no_show,
    )
