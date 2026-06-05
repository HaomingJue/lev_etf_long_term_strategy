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
from backtester import run_backtest, print_results, cagr as compute_cagr, _tbill_daily


# ──────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────

PRESETS = {
    "QQQ": {"base": "QQQ", "lev2": "QLD",  "lev3": "TQQQ", "dd_start": 2010},
    "SPY": {"base": "SPY", "lev2": "SSO",  "lev3": "UPRO", "dd_start": 2009},
    "IWM": {"base": "IWM", "lev2": "UWM",  "lev3": "TNA",  "dd_start": 2009},
}

START_DATE    = "2003-01-01"
WARMUP_START  = "2001-01-01"   # download start — ensures MA200 is warm by START_DATE
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

# Annual MERs — applied to synthetic pre-inception period only (real prices include MER)
_MER_3X = {"QQQ": 0.0095, "SPY": 0.0091, "IWM": 0.0109}
_MER_2X = {"QQQ": 0.0095, "SPY": 0.0089, "IWM": 0.0095}


def _build_lev_nav(base: pd.Series, real: pd.Series, L: int,
                   annual_mer: float = 0.0) -> pd.Series:
    ret       = base.pct_change().fillna(0)
    var20     = ret.rolling(20).var().fillna(0)
    daily_mer = annual_mer / 252.0

    # Determine stitch point before building synthetic
    first_real_pos = None
    if real is not None and not real.dropna().empty:
        common = base.index.intersection(real.dropna().index)
        if not common.empty:
            first_real_pos = base.index.get_loc(common[0])

    nav = np.ones(len(base))
    for i in range(1, len(base)):
        lev_r = L * ret.values[i] - 0.5 * (L**2 - L) * var20.values[i]
        if first_real_pos is None or i < first_real_pos:
            lev_r -= daily_mer   # MER only during synthetic period
        nav[i] = nav[i-1] * (1.0 + lev_r)

    synth = pd.Series(nav, index=base.index)
    if first_real_pos is None:
        return synth
    first    = base.index[first_real_pos]
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
            s = yf.download(tk, start=WARMUP_START, end=end,
                            auto_adjust=True, progress=False)["Close"].squeeze().dropna()
            s.name = tk
            return s
        except Exception:
            return pd.Series(dtype=float)

    base = dl(base_tk)
    df   = pd.DataFrame({
        "base": base,
        "lev2": _build_lev_nav(base, dl(lev2_tk), 2, annual_mer=_MER_2X[preset]),
        "lev3": _build_lev_nav(base, dl(lev3_tk), 3, annual_mer=_MER_3X[preset]),
    }).dropna(subset=["base"])
    df["ret"]   = df["base"].pct_change().fillna(0)
    df["MA100"] = df["base"].rolling(100).mean()
    df["MA200"] = df["base"].rolling(200).mean()
    df = df[df.index >= START_DATE].copy()
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


def _opt_backtest(df: pd.DataFrame, entry, drop, exit_, buy, ab, ax2,
                  exit_ma: int = 200):
    ax3  = 1.0 - ax2
    f    = df.iloc[0]
    nb   = df["base"].values / f["base"]
    n2   = df["lev2"].values / f["lev2"]
    n3   = df["lev3"].values / f["lev3"]
    ma_arm  = df["MA200"].values / f["base"]              # arm always uses MA200
    ma_exit = df[f"MA{exit_ma}"].values / f["base"]       # exit uses selected MA

    cash = CAPITAL
    s_b = s_2 = s_3 = 0.0
    armed = bf = bt = False
    port  = np.empty(len(df))
    port[0] = CAPITAL

    for i in range(1, len(df)):
        if (np.isnan(ma_arm[i]) or ma_arm[i] == 0
                or np.isnan(ma_exit[i]) or ma_exit[i] == 0):
            port[i] = port[i-1]
            continue

        vb = s_b * nb[i]; v2 = s_2 * n2[i]; v3 = s_3 * n3[i]
        tot = cash + vb + v2 + v3

        if nb[i] < ma_exit[i] * exit_ and (s_2 > 0 or s_3 > 0):
            cash += v2 + v3
            s_2 = s_3 = 0.0
            if not bt and ab > 0:
                vb  = s_b * nb[i]; tot = cash + vb; tgt = tot * ab
                if vb > tgt + 0.01:
                    s_b -= (vb - tgt) / nb[i]; cash += vb - tgt
                bt = True
            armed = False
        else:
            if not armed and nb[i] > ma_arm[i] * entry:
                armed = True
            d = (nb[i-1] - nb[i]) / nb[i-1] if nb[i-1] > 0 else 0.0
            if armed and nb[i] > ma_arm[i] * entry and d >= drop and cash > 0.01:
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

# Default secondary-sort tolerance per preset.
# All disabled by default (plain top-CAGR). Enable for QQQ with --tie-tolerance 0.01
# to trade ~4pp CAGR over 12 years for ~20pp better max drawdown.
# See section 6.2 of README for the honest cost-benefit.
_DEFAULT_TIE_TOLERANCE = {"QQQ": 0.0, "SPY": 0.0, "IWM": 0.0}


def build_param_schedule(preset: str, start_year: int, end_year: int,
                         df_full: pd.DataFrame, exit_ma: int = 200,
                         tie_tolerance: float | None = None) -> dict:
    """
    Build the per-year param schedule.

    tie_tolerance : float | None
        If > 0, applies a secondary-sort rule: from combos within
        `tie_tolerance` CAGR (fraction, e.g. 0.01 = 1pp) of the top combo,
        pick the one with the best (least negative) worst calendar year.
        If None, uses the preset default (QQQ=0.01, SPY/IWM=0.0).
        If 0.0, plain top-CAGR ranking (legacy behavior).
    """
    if tie_tolerance is None:
        tie_tolerance = _DEFAULT_TIE_TOLERANCE.get(preset, 0.0)

    dd_start = PRESETS[preset]["dd_start"]
    grid     = _build_grid()
    schedule = {}

    rule_note = (f"plain top-CAGR" if tie_tolerance <= 0
                 else f"secondary-sort within {tie_tolerance*100:.1f}pp CAGR")
    print(f"\nPhase 1 — building param schedule ({preset}, "
          f"{start_year}–{end_year}, exit_ma=MA{exit_ma}, {rule_note})")
    print(f"  {len(grid):,} combos × {end_year - start_year + 1} training windows\n")

    for trade_yr in range(start_year, end_year + 1):
        train_end = trade_yr - 1
        df_train  = df_full[df_full.index.year <= train_end]
        if len(df_train) < 250:
            print(f"  {trade_yr}: insufficient training data — skipped")
            continue

        # Collect ALL passing combos for this year so we can apply secondary sort
        passing = []  # list of (cagr, worst_year, params_tuple)
        for entry, drop, exit_, buy, ab, ax2 in tqdm(
                grid, desc=f"  2003–{train_end}", leave=False):
            c, port  = _opt_backtest(df_train, entry, drop, exit_, buy, ab, ax2,
                                     exit_ma=exit_ma)
            ok, worst = _check_dd(df_train, port, dd_start)
            if ok:
                passing.append((c, worst, (entry, drop, exit_, buy, ab, ax2)))

        if not passing:
            print(f"  {trade_yr}: no passing combo found")
            continue

        # Plain top-CAGR pick
        leader = max(passing, key=lambda r: r[0])
        leader_cagr = leader[0]

        # Secondary-sort: within tolerance, pick best worst-year
        if tie_tolerance > 0:
            within = [r for r in passing if leader_cagr - r[0] <= tie_tolerance]
            chosen = max(within, key=lambda r: r[1])  # max worst-year = least negative
        else:
            chosen = leader

        c, worst, (entry, drop, exit_, buy, ab, ax2) = chosen
        best = dict(
            entry_signal=entry, drop_level=drop,
            exit_signal=exit_,  buy_pct=buy,
            alloc_base=ab,      alloc_x2=ax2,
            alloc_x3=round(1 - ax2, 4),
            train_cagr=round(c * 100, 2),
            train_worst_year=round(worst * 100, 2),
        )
        schedule[trade_yr] = best

        # Annotate if secondary-sort changed the pick
        changed = tie_tolerance > 0 and chosen[2] != leader[2]
        tag = "  [tie-break]" if changed else ""
        if changed:
            cagr_cost = (leader_cagr - c) * 100
            ulcer_gain = (worst - leader[1]) * 100
            tag = f"  [tie-break: -{cagr_cost:.2f}pp CAGR, +{ulcer_gain:.1f}pp worst-yr]"
        print(f"  {trade_yr}  train=2003–{train_end}  "
              f"entry={best['entry_signal']}  drop={best['drop_level']}  "
              f"exit={best['exit_signal']}  buy={best['buy_pct']}  "
              f"CAGR={best['train_cagr']:.1f}%  worst={best['train_worst_year']:.1f}%{tag}")

    return schedule


# ──────────────────────────────────────────────────────────────
# PHASE 2 — walk-forward backtest
# ──────────────────────────────────────────────────────────────

def run_walkforward(preset: str, schedule: dict,
                    start_year: int, end_year: int,
                    capital: float, no_show: bool,
                    exit_ma: int = 200, tie_tolerance: float = 0.0):

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
        no_show=no_show,
        save_plot=None,
    )

    print(f"\nPhase 2 — walk-forward backtest ({preset}, {start_year}–{end_year}, "
          f"exit_ma=MA{exit_ma})\n")
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
    # Tie-break runs add _tiebreak so Highest CAGR and Balanced variants
    # never collide on the same output files.
    ma_tag  = "" if exit_ma == 200 else f"_ma{exit_ma}"
    tie_tag = "_tiebreak" if tie_tolerance > 0 else ""
    slug    = f"{preset}_walkforward_{start_year}-{end_year}{ma_tag}{tie_tag}"
    year_df.to_csv(out_dir / f"{slug}_yearly.csv", index=False)
    print(f"\n  Saved: results/walkforward/{slug}_yearly.csv")

    _save_command_log(int_sched, preset, start_year, end_year,
                      out_dir / f"{slug}_commands.txt", exit_ma=exit_ma)

    # Fixed model: 2003-(start_year-1) params frozen for the full period
    print(f"\nRunning fixed model (2003-{start_year-1} params, frozen) for comparison …")
    hist_fixed, year_df_fixed = _run_fixed_model(
        preset, p0, start_year, end_year, capital, exit_ma=exit_ma)

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
                     exit_ma: int = 200):
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
    p.add_argument("--exit-ma",    type=int, default=200, choices=[100, 200],
                   help="MA period used for exit signal "
                        "(arm/entry always uses MA200)")
    p.add_argument("--tie-tolerance", type=float, default=None,
                   help="Secondary-sort tolerance in CAGR fraction (e.g. 0.01 = 1pp). "
                        "From combos within this CAGR of the leader, pick the one "
                        "with the best worst calendar year. "
                        "Defaults: QQQ=0.01, SPY/IWM=0.0. Pass 0.0 to force plain top-CAGR.")
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

    # Schedule path: MA200 keeps the original filename for back-compat;
    # non-200 adds _ma{N}; tie-break runs (tolerance > 0) add _tiebreak so
    # the Highest CAGR and Balanced variants never collide on the same file.
    _tie_tol_resolved = (args.tie_tolerance
                         if args.tie_tolerance is not None
                         else _DEFAULT_TIE_TOLERANCE.get(args.preset, 0.0))
    sched_suffix  = "" if args.exit_ma == 200 else f"_ma{args.exit_ma}"
    if _tie_tol_resolved > 0:
        sched_suffix += "_tiebreak"
    schedule_path = out_dir / f"{args.preset}_param_schedule{sched_suffix}.json"

    if args.no_rebuild and schedule_path.exists():
        print(f"Loading cached schedule: {schedule_path}")
        schedule = json.loads(schedule_path.read_text())
    else:
        # For --only-year, just need data through that year's training cutoff.
        end_buffer = (f"{args.only_year}-01-15"
                      if args.only_year is not None
                      else f"{args.end_year}-12-31")
        df_full  = load_full_data(args.preset, end_buffer)
        schedule = build_param_schedule(
            args.preset, args.start_year, args.end_year, df_full,
            exit_ma=args.exit_ma, tie_tolerance=args.tie_tolerance)

        if args.only_year is not None:
            # Merge into existing schedule — preserve all other years.
            existing = {}
            if schedule_path.exists():
                existing = json.loads(schedule_path.read_text())
            for yr_int, row in schedule.items():
                existing[str(yr_int)] = row
            schedule_path.write_text(json.dumps(existing, indent=2))
            years_now = sorted(int(k) for k in existing.keys())
            print(f"\n  Merged year {args.only_year} into {schedule_path}")
            print(f"  Schedule now spans: {years_now[0]}–{years_now[-1]} "
                  f"({len(years_now)} rows)")
        else:
            schedule_path.write_text(json.dumps(schedule, indent=2))
            print(f"\n  Saved schedule: {schedule_path}")

    # --only-year: skip Phase 2 — there's no meaningful continuous backtest window
    if args.only_year is not None:
        print(f"  Phase 2 skipped (--only-year mode).")
        sys.exit(0)

    run_walkforward(
        args.preset, schedule,
        args.start_year, args.end_year,
        args.capital, args.no_show,
        exit_ma=args.exit_ma,
        tie_tolerance=_tie_tol_resolved,
    )
