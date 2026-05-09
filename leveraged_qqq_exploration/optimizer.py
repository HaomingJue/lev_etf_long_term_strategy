# ==========================================================
# FULL-HISTORY OPTIMIZER  —  QQQ / QLD / TQQQ strategy
#
# Runs every parameter combo against the FULL history once.
#
# A combo PASSES if, at the end of every calendar year,
# the portfolio is within 40% of its all-time peak to that date.
#
# Passing combos are ranked by CAGR (highest first).
#
# Output:
#   - Console leaderboard (top 20)
#   - optimizer_results.csv   (all combos, pass/fail flagged)
#   - optimizer_equity.png    (equity curves of top 5)
#   - optimizer_scatter.png   (CAGR vs DD scatter)
# ==========================================================

import itertools
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from tqdm import tqdm

warnings.filterwarnings("ignore")

OUT_DIR = Path(__file__).parent

# ----------------------------------------------------------
# SYNTHETIC LEVERAGED NAV BUILDER
#
# For dates before a real ETF existed, we simulate its NAV
# from QQQ daily returns using the standard leverage model:
#   lev_daily_ret = L*r - 0.5*(L^2 - L)*rolling_var20
#
# For dates where real prices exist, we use those instead.
# The two segments are stitched so the real series continues
# smoothly from the last synthetic value.
# ----------------------------------------------------------

def build_lev_nav(qqq: pd.Series, real: pd.Series, L: int) -> pd.Series:
    """
    Returns a NAV series (starting at 1.0 on qqq.index[0]) for an
    L-times leveraged ETF, using synthetic returns before real ETF
    inception and real prices (re-scaled) from inception onward.
    """
    ret   = qqq.pct_change().fillna(0)
    var20 = ret.rolling(20).var().fillna(0)

    nav = np.empty(len(qqq))
    nav[0] = 1.0
    r_arr = ret.values
    v_arr = var20.values
    for i in range(1, len(qqq)):
        r = r_arr[i]; v = v_arr[i]
        lev_r = L * r - 0.5 * (L**2 - L) * v
        nav[i] = nav[i-1] * (1.0 + lev_r)
    synth = pd.Series(nav, index=qqq.index, name=f"synth{L}x")

    if real is None or real.dropna().empty:
        return synth

    common = qqq.index.intersection(real.dropna().index)
    if common.empty:
        return synth

    first_real = common[0]
    scale = synth.loc[first_real] / real.loc[first_real]
    real_scaled = real.reindex(qqq.index) * scale

    stitched = synth.copy()
    stitched.loc[first_real:] = real_scaled.loc[first_real:]
    return stitched


# ----------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------

START_DATA  = "2003-01-01"   # QQQ inception — synthetic lev before real ETF dates
END         = "2026-05-08"
CAPITAL     = 10_000
DD_LIMIT      = 0.40    # year-end drawdown cap
DD_START_YEAR = 2010    # only apply DD filter from this year onward
                        # (synthetic lev data before TQQQ inception is too punishing)

# Grid axes
ENTRY_SIGNALS = [1.01, 1.02, 1.03, 1.04, 1.05, 1.06]
DROP_LEVELS   = [0.005, 0.010, 0.015, 0.020, 0.025, 0.030]
EXIT_SIGNALS  = [0.95, 0.97, 0.99, 1.00, 1.01, 1.02]
BUY_PCTS      = [0.10, 0.20, 0.30, 0.40]
ALLOC_BASES   = [0.0, 0.10, 0.20, 0.30]       # QQQ fraction of portfolio
ALLOC_X2S     = [0.0, 0.25, 0.50, 0.75, 1.0]  # fraction of lev spend → QLD


# ----------------------------------------------------------
# DATA
# ----------------------------------------------------------

def download(ticker, start, end):
    raw = yf.download(ticker, start=start, end=end,
                      auto_adjust=True, progress=False)
    s = raw["Close"].squeeze().dropna()
    s.name = ticker
    return s


def load_data():
    # Download QQQ from 1999 inception; real lev ETFs from their launch dates.
    # Synthetic NAVs fill the gap before real ETFs existed.
    print("Downloading QQQ, QLD, TQQQ …")
    qqq = download("QQQ", "2003-01-01", END)
    try:
        qld  = download("QLD",  "2003-01-01", END)
    except Exception:
        qld  = pd.Series(dtype=float)
    try:
        tqqq = download("TQQQ", "2003-01-01", END)
    except Exception:
        tqqq = pd.Series(dtype=float)

    # Stitch synthetic + real for lev ETFs
    lev2_nav = build_lev_nav(qqq, qld,  2)
    lev3_nav = build_lev_nav(qqq, tqqq, 3)

    df = pd.DataFrame({
        "QQQ":  qqq,
        "QLD":  lev2_nav,
        "TQQQ": lev3_nav,
    }).dropna(subset=["QQQ"])

    df.columns = ["QQQ", "QLD", "TQQQ"]
    df = df / df.iloc[0]                        # normalise to 1.0 on day 0
    df["ret"]   = df["QQQ"].pct_change().fillna(0)
    df["MA200"] = df["QQQ"].rolling(200).mean()
    return df


# ----------------------------------------------------------
# BACKTEST  (single full-history run)
# Returns (cagr, portfolio_array, qqq_nav_array)
# ----------------------------------------------------------

def backtest(df, entry_signal, drop_level, exit_signal,
             buy_pct, alloc_base, alloc_x2):
    first   = df.iloc[0]
    nb_arr  = df["QQQ"].values  / first["QQQ"]
    n2_arr  = df["QLD"].values  / first["QLD"]
    n3_arr  = df["TQQQ"].values / first["TQQQ"]
    ma_arr  = df["MA200"].values / first["QQQ"]
    n       = len(df)

    alloc_x3 = 1.0 - alloc_x2

    cash         = CAPITAL
    s_b = s_2 = s_3 = 0.0
    armed        = False
    base_filled  = False
    base_trimmed = False
    lev_pw = lev_dw = 0.0

    portfolio = np.empty(n)
    portfolio[0] = CAPITAL

    for i in range(1, n):
        nb    = nb_arr[i];  n2 = n2_arr[i];  n3 = n3_arr[i]
        price = nb_arr[i]
        prev  = nb_arr[i-1]
        ma    = ma_arr[i]

        if np.isnan(ma) or ma == 0:
            portfolio[i] = portfolio[i-1]
            continue

        val_b = s_b * nb;  val_2 = s_2 * n2;  val_3 = s_3 * n3
        total = cash + val_b + val_2 + val_3

        # EXIT
        if price < ma * exit_signal and (s_2 > 0 or s_3 > 0 or s_b > 0):
            lev_val = val_2 + val_3
            if lev_val > 0:
                cash += lev_val
                s_2 = s_3 = lev_pw = lev_dw = 0.0

            if not base_trimmed and alloc_base > 0:
                val_b  = s_b * nb
                total  = cash + val_b
                target = total * alloc_base
                if val_b > target + 0.01:
                    excess = val_b - target
                    s_b   -= excess / nb
                    cash  += excess
                base_trimmed = True

            armed = False

        else:
            if not armed and price > ma * entry_signal:
                armed = True

            drop = (prev - price) / prev if prev > 0 else 0.0
            if armed and price > ma * entry_signal \
                    and drop >= drop_level and cash > 0.01:

                total = cash + s_b * nb + s_2 * n2 + s_3 * n3

                if not base_filled and alloc_base > 0:
                    base_spend = min(max(total * alloc_base - s_b * nb, 0), cash)
                    if base_spend > 0.01:
                        s_b  += base_spend / nb
                        cash -= base_spend
                    base_filled = True
                    total = cash + s_b * nb + s_2 * n2 + s_3 * n3

                lev_spend = min(buy_pct * total, cash)
                if lev_spend > 0.01:
                    a2, a3 = lev_spend * alloc_x2, lev_spend * alloc_x3
                    if a2 > 0:
                        s_2 += a2 / n2;  lev_pw += price * a2;  lev_dw += a2
                    if a3 > 0:
                        s_3 += a3 / n3;  lev_pw += price * a3;  lev_dw += a3
                    cash -= lev_spend

        portfolio[i] = cash + s_b * nb + s_2 * n2 + s_3 * n3

    days = (df.index[-1] - df.index[0]).days
    cagr = (portfolio[-1] / CAPITAL) ** (365.25 / days) - 1 if days > 0 else 0.0
    return cagr, portfolio, nb_arr


# ----------------------------------------------------------
# DD FILTER
# ----------------------------------------------------------

def check_dd(df, portfolio, qqq_nav):
    """
    A combo passes if, in every calendar year from DD_START_YEAR onward,
    the year-end portfolio value is no more than DD_LIMIT (40%) below
    the year-start portfolio value.
    i.e. annual return >= -40% in every year.

    Returns (passed, worst_annual_dd, diagnostics_list).
    """
    idx   = df.index
    years = np.unique(idx.year)
    diag  = []

    for yr in years:
        yr_mask   = idx.year == yr
        yr_idxs   = np.where(yr_mask)[0]
        first_idx = yr_idxs[0]
        last_idx  = yr_idxs[-1]

        port_boy  = portfolio[first_idx]   # start of year
        port_eoy  = portfolio[last_idx]    # end of year
        ann_ret   = (port_eoy - port_boy) / port_boy   # negative = loss

        # Only enforce limit from DD_START_YEAR onward
        if yr < DD_START_YEAR:
            diag.append(dict(year=yr, port_boy=round(port_boy,2),
                             port_eoy=round(port_eoy,2),
                             ann_ret=round(ann_ret*100,2), passed=True))
            continue

        passed = ann_ret >= -DD_LIMIT
        diag.append(dict(year=yr,
                         port_boy=round(port_boy, 2),
                         port_eoy=round(port_eoy, 2),
                         ann_ret=round(ann_ret * 100, 2),
                         passed=passed))
        if not passed:
            return False, ann_ret, diag

    worst = min(d["ann_ret"] for d in diag)
    return True, worst / 100, diag



# ----------------------------------------------------------
# GRID
# ----------------------------------------------------------

def build_grid():
    grid = []
    for entry, drop, exit_, buy, ab, ax2 in itertools.product(
        ENTRY_SIGNALS, DROP_LEVELS, EXIT_SIGNALS,
        BUY_PCTS, ALLOC_BASES, ALLOC_X2S
    ):
        if exit_ >= entry:
            continue
        grid.append((entry, drop, exit_, buy, ab, ax2))
    return grid


# ----------------------------------------------------------
# MAIN SEARCH
# ----------------------------------------------------------

def run_search(df, grid):
    records = []
    for params in tqdm(grid, desc="Scanning combos"):
        entry, drop, exit_, buy, ab, ax2 = params
        cagr, port, nav = backtest(df, entry, drop, exit_, buy, ab, ax2)
        passed, abs_dd, diag = check_dd(df, port, nav)

        records.append({
            "entry_signal": entry,
            "drop_level":   drop,
            "exit_signal":  exit_,
            "buy_pct":      buy,
            "alloc_base":   ab,
            "alloc_x2":     ax2,
            "alloc_x3":     round(1 - ax2, 4),
            "cagr":         round(cagr * 100, 2),
            "worst_ann_ret":  round(abs_dd * 100, 2),
            
            "passed":       passed,
        })

    return pd.DataFrame(records)


# ----------------------------------------------------------
# PLOTS
# ----------------------------------------------------------

def plot_top_combos(df, results, n=5):
    top = results[results["passed"]].nlargest(n, "cagr")
    fig, ax = plt.subplots(figsize=(14, 7))

    bh = CAPITAL * df["QQQ"] / df["QQQ"].iloc[0]
    ax.plot(df.index, bh, label="QQQ Buy & Hold",
            color="steelblue", linewidth=1.8, linestyle="--")

    colors = plt.cm.tab10(np.linspace(0, 0.7, n))
    for rank, (_, row) in enumerate(top.iterrows()):
        cagr, port, _ = backtest(df, row["entry_signal"], row["drop_level"],
                                 row["exit_signal"], row["buy_pct"],
                                 row["alloc_base"], row["alloc_x2"])
        label = (f"#{rank+1}  e={row['entry_signal']} d={row['drop_level']} "
                 f"x={row['exit_signal']} b={row['buy_pct']}  "
                 f"QQQ={row['alloc_base']} QLD={row['alloc_x2']} "
                 f"TQQQ={row['alloc_x3']}  "
                 f"CAGR={row['cagr']:.1f}% DD={row['worst_ann_ret']:.1f}%")
        ax.plot(df.index, port, label=label, color=colors[rank], linewidth=1.2)

    ax.set_title("Top combos — full history equity curves", fontsize=11)
    ax.set_ylabel("Portfolio Value ($)")
    ax.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax.legend(fontsize=7, loc="upper left")
    ax.grid(True, alpha=0.35)
    plt.tight_layout()
    plt.savefig(OUT_DIR / "optimizer_equity.png", dpi=150)
    print("  Saved: optimizer_equity.png")
    plt.show()


def plot_scatter(results):
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
    ax.set_title("CAGR vs Worst Annual Return — all combos")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(OUT_DIR / "optimizer_scatter.png", dpi=150)
    print("  Saved: optimizer_scatter.png")
    plt.show()


# ----------------------------------------------------------
# ENTRY POINT
# ----------------------------------------------------------

if __name__ == "__main__":
    df = load_data()
    # Keep full history so CAGR denominator starts from day 1
    # MA200 NaN rows are already skipped inside backtest() via isnan check

    print(f"\nData range  : {df.index[0].date()} → {df.index[-1].date()}")
    print(f"Trading days: {len(df)}")
    print(f"Year-end DD cap : {DD_LIMIT*100:.0f}% max annual loss\n")

    grid = build_grid()
    print(f"Grid size   : {len(grid):,} combos\n")

    results = run_search(df, grid)
    results.to_csv(OUT_DIR / "optimizer_results.csv", index=False)
    print(f"\n  Saved: optimizer_results.csv  ({len(results):,} rows)")

    passing = results[results["passed"]].sort_values("cagr", ascending=False)
    print(f"  Passing combos: {len(passing):,} / {len(results):,}")

    print("\n" + "=" * 105)
    print(f"  LEADERBOARD — top 20 passing combos, ranked by CAGR")
    print(f"  Filter: year-end DD ≤ {DD_LIMIT*100:.0f}% from {DD_START_YEAR} onward (synthetic pre-{DD_START_YEAR} CAGR only)")
    print("=" * 105)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 120)
    print(passing.head(20).to_string(index=False))

    if not passing.empty:
        best = passing.iloc[0]
        print("\n" + "=" * 105)
        print("  BEST COMBO")
        print("=" * 105)
        print(f"  entry={best['entry_signal']}  drop={best['drop_level']}  "
              f"exit={best['exit_signal']}  buy={best['buy_pct']}")
        print(f"  QQQ={best['alloc_base']*100:.0f}%  "
              f"QLD={best['alloc_x2']*100:.0f}%  "
              f"TQQQ={best['alloc_x3']*100:.0f}%")
        print(f"  CAGR        : {best['cagr']:.2f}%")
        print(f"  Worst annual return : {best['worst_ann_ret']:.2f}%")

        # Show year-end DD breakdown for best combo
        cagr, port, nav = backtest(df, best["entry_signal"], best["drop_level"],
                                   best["exit_signal"], best["buy_pct"],
                                   best["alloc_base"], best["alloc_x2"])
        _, _, diag = check_dd(df, port, nav)
        if diag:
            print("\n  Year-end drawdown check:")
            for d in diag:
                flag = "✓" if d["passed"] else "✗"
                print(f"    {d['year']}  start ${d['port_boy']:,.0f}  "
                      f"end ${d['port_eoy']:,.0f}  "
                      f"return {d['ann_ret']:+.1f}%  {flag}")

        # Baselines
        print("\n" + "=" * 105)
        print("  BASELINE COMPARISONS (full history)")
        print("=" * 105)
        for label, p in [
            ("Original  entry=1.04 drop=0.01 exit=1.00 buy=0.20  0% QQQ 0% QLD 100% TQQQ",
             (1.04, 0.01, 1.00, 0.20, 0.0, 0.0)),
            ("Prev best entry=1.01 drop=0.01 exit=0.99 buy=0.10 30% QQQ 25% QLD 75% TQQQ",
             (1.01, 0.01, 0.99, 0.10, 0.30, 0.25)),
        ]:
            c, port_b, nav_b = backtest(df, *p)
            ok, add, dg = check_dd(df, port_b, nav_b)
            status = "PASS" if ok else "FAIL"
            print(f"\n  {label}")
            print(f"    CAGR {c*100:.2f}%   worst annual return {add*100:.2f}%   {status}")
            for d in dg:
                flag = "✓" if d["passed"] else "✗"
                print(f"      {d['year']}  start ${d['port_boy']:,.0f}  "
                      f"end ${d['port_eoy']:,.0f}  "
                      f"return {d['ann_ret']:+.1f}%  {flag}")

        print("\nPlotting …")
        plot_top_combos(df, results, n=5)
        plot_scatter(results)