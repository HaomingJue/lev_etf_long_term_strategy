# ==========================================================
# MA50-EXIT RESEARCH  —  SPY / SSO / UPRO strategy
#
#   ARM / BUY  : MA200  (slow trend filter)
#   EXIT       : MA50   (faster reaction to trend breaks)
#
# Outputs:
#   - ma50_exit_results.csv
#   - ma50_exit_equity.png
#   - ma50_exit_scatter.png
#   - Head-to-head MA200 vs MA50 exit comparison
# ==========================================================

import itertools
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
import sys
_no_show = "--no-show" in sys.argv
import matplotlib
if _no_show:
    matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from tqdm import tqdm

warnings.filterwarnings("ignore")

OUT_DIR = Path(__file__).parent / "ma50"
OUT_DIR.mkdir(exist_ok=True)


def build_lev_nav(base: pd.Series, real: pd.Series, L: int) -> pd.Series:
    ret   = base.pct_change().fillna(0)
    var20 = ret.rolling(20).var().fillna(0)
    nav   = np.empty(len(base))
    nav[0] = 1.0
    r_arr, v_arr = ret.values, var20.values
    for i in range(1, len(base)):
        r = r_arr[i]; v = v_arr[i]
        nav[i] = nav[i-1] * (1.0 + L * r - 0.5 * (L**2 - L) * v)
    synth = pd.Series(nav, index=base.index, name=f"synth{L}x")
    if real is None or real.dropna().empty:
        return synth
    common = base.index.intersection(real.dropna().index)
    if common.empty:
        return synth
    first_real  = common[0]
    scale       = synth.loc[first_real] / real.loc[first_real]
    real_scaled = real.reindex(base.index) * scale
    stitched    = synth.copy()
    stitched.loc[first_real:] = real_scaled.loc[first_real:]
    return stitched


START_DATE    = "2003-01-01"
WARMUP_START  = "2001-01-01"   # download start — ensures MA200 is warm by START_DATE
END           = "2026-05-08"
CAPITAL       = 10_000
DD_LIMIT      = 0.40
DD_START_YEAR = 2009

ENTRY_SIGNALS = [1.01, 1.02, 1.03, 1.04, 1.05, 1.06]
DROP_LEVELS   = [0.0, 0.0025, 0.005, 0.010, 0.015, 0.020, 0.025, 0.030]  # v2 grid: extended past the binding 0.005 edge (README s8)
EXIT_SIGNALS  = [0.95, 0.97, 0.99, 1.00, 1.01, 1.02]
BUY_PCTS      = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60]  # v2 grid: extended past the binding 0.40 edge
ALLOC_BASES   = [0.0, 0.10, 0.20, 0.30]
ALLOC_X2S     = [0.0, 0.25, 0.50, 0.75, 1.0]


def download(ticker, start, end):
    raw = yf.download(ticker, start=start, end=end,
                      auto_adjust=True, progress=False)
    s = raw["Close"].squeeze().dropna()
    s.name = ticker
    return s


def load_data():
    print("Downloading SPY, SSO, UPRO ...")
    spy = download("SPY", WARMUP_START, END)
    try:
        sso  = download("SSO",  WARMUP_START, END)
    except Exception:
        sso  = pd.Series(dtype=float)
    try:
        upro = download("UPRO", WARMUP_START, END)
    except Exception:
        upro = pd.Series(dtype=float)
    lev2_nav = build_lev_nav(spy, sso,  2)
    lev3_nav = build_lev_nav(spy, upro, 3)
    df = pd.DataFrame({"SPY": spy, "SSO": lev2_nav, "UPRO": lev3_nav}
                      ).dropna(subset=["SPY"])
    df = df / df.iloc[0]
    df["ret"]   = df["SPY"].pct_change().fillna(0)
    df["MA200"] = df["SPY"].rolling(200).mean()
    df["MA50"]  = df["SPY"].rolling(50).mean()
    df = df[df.index >= START_DATE].copy()
    return df


def backtest(df, entry_signal, drop_level, exit_signal,
             buy_pct, alloc_base, alloc_x2):
    first      = df.iloc[0]
    nb_arr     = df["SPY"].values  / first["SPY"]
    n2_arr     = df["SSO"].values  / first["SSO"]
    n3_arr     = df["UPRO"].values / first["UPRO"]
    ma200_arr  = df["MA200"].values / first["SPY"]
    ma50_arr   = df["MA50"].values  / first["SPY"]
    n          = len(df)
    alloc_x3   = 1.0 - alloc_x2
    cash       = CAPITAL
    s_b = s_2 = s_3 = 0.0
    armed = base_filled = base_trimmed = False
    lev_pw = lev_dw = 0.0
    portfolio    = np.empty(n)
    portfolio[0] = CAPITAL
    for i in range(1, n):
        nb    = nb_arr[i];  n2 = n2_arr[i];  n3 = n3_arr[i]
        price = nb_arr[i];  prev = nb_arr[i-1]
        ma200 = ma200_arr[i];  ma50 = ma50_arr[i]
        if np.isnan(ma200) or ma200 == 0 or np.isnan(ma50) or ma50 == 0:
            portfolio[i] = portfolio[i-1]; continue
        val_b = s_b*nb; val_2 = s_2*n2; val_3 = s_3*n3
        if price < ma50 * exit_signal and (s_2 > 0 or s_3 > 0):
            cash += val_2 + val_3
            s_2 = s_3 = lev_pw = lev_dw = 0.0
            if not base_trimmed and alloc_base > 0:
                val_b = s_b*nb; total = cash+val_b; target = total*alloc_base
                if val_b > target+0.01:
                    excess = val_b-target; s_b -= excess/nb; cash += excess
                base_trimmed = True
            armed = False
        else:
            if not armed and price > ma200 * entry_signal:
                armed = True
            drop = (prev-price)/prev if prev > 0 else 0.0
            if armed and price > ma200*entry_signal and drop >= drop_level and cash > 0.01:
                total = cash + s_b*nb + s_2*n2 + s_3*n3
                if not base_filled and alloc_base > 0:
                    base_spend = min(max(total*alloc_base - s_b*nb, 0), cash)
                    if base_spend > 0.01: s_b += base_spend/nb; cash -= base_spend
                    base_filled = True
                    total = cash + s_b*nb + s_2*n2 + s_3*n3
                lev_spend = min(buy_pct*total, cash)
                if lev_spend > 0.01:
                    a2, a3 = lev_spend*alloc_x2, lev_spend*alloc_x3
                    if a2 > 0: s_2 += a2/n2; lev_pw += price*a2; lev_dw += a2
                    if a3 > 0: s_3 += a3/n3; lev_pw += price*a3; lev_dw += a3
                    cash -= lev_spend
        portfolio[i] = cash + s_b*nb + s_2*n2 + s_3*n3
    days = (df.index[-1] - df.index[0]).days
    cagr = (portfolio[-1] / CAPITAL) ** (365.25 / days) - 1 if days > 0 else 0.0
    return cagr, portfolio, nb_arr


def backtest_ma200_exit(df, entry_signal, drop_level, exit_signal,
                        buy_pct, alloc_base, alloc_x2):
    first      = df.iloc[0]
    nb_arr     = df["SPY"].values  / first["SPY"]
    n2_arr     = df["SSO"].values  / first["SSO"]
    n3_arr     = df["UPRO"].values / first["UPRO"]
    ma200_arr  = df["MA200"].values / first["SPY"]
    n          = len(df)
    alloc_x3   = 1.0 - alloc_x2
    cash       = CAPITAL
    s_b = s_2 = s_3 = 0.0
    armed = base_filled = base_trimmed = False
    lev_pw = lev_dw = 0.0
    portfolio    = np.empty(n)
    portfolio[0] = CAPITAL
    for i in range(1, n):
        nb    = nb_arr[i];  n2 = n2_arr[i];  n3 = n3_arr[i]
        price = nb_arr[i];  prev = nb_arr[i-1]
        ma    = ma200_arr[i]
        if np.isnan(ma) or ma == 0:
            portfolio[i] = portfolio[i-1]; continue
        val_b = s_b*nb; val_2 = s_2*n2; val_3 = s_3*n3
        if price < ma * exit_signal and (s_2 > 0 or s_3 > 0):
            cash += val_2 + val_3
            s_2 = s_3 = lev_pw = lev_dw = 0.0
            if not base_trimmed and alloc_base > 0:
                val_b = s_b*nb; total = cash+val_b; target = total*alloc_base
                if val_b > target+0.01:
                    excess = val_b-target; s_b -= excess/nb; cash += excess
                base_trimmed = True
            armed = False
        else:
            if not armed and price > ma * entry_signal:
                armed = True
            drop = (prev-price)/prev if prev > 0 else 0.0
            if armed and price > ma*entry_signal and drop >= drop_level and cash > 0.01:
                total = cash + s_b*nb + s_2*n2 + s_3*n3
                if not base_filled and alloc_base > 0:
                    base_spend = min(max(total*alloc_base - s_b*nb, 0), cash)
                    if base_spend > 0.01: s_b += base_spend/nb; cash -= base_spend
                    base_filled = True
                    total = cash + s_b*nb + s_2*n2 + s_3*n3
                lev_spend = min(buy_pct*total, cash)
                if lev_spend > 0.01:
                    a2, a3 = lev_spend*alloc_x2, lev_spend*alloc_x3
                    if a2 > 0: s_2 += a2/n2; lev_pw += price*a2; lev_dw += a2
                    if a3 > 0: s_3 += a3/n3; lev_pw += price*a3; lev_dw += a3
                    cash -= lev_spend
        portfolio[i] = cash + s_b*nb + s_2*n2 + s_3*n3
    days = (df.index[-1] - df.index[0]).days
    cagr = (portfolio[-1] / CAPITAL) ** (365.25 / days) - 1 if days > 0 else 0.0
    return cagr, portfolio, nb_arr


def check_dd(df, portfolio, nav):
    idx = df.index; years = np.unique(idx.year); diag = []
    for yr in years:
        yr_idxs  = np.where(idx.year == yr)[0]
        port_boy = portfolio[yr_idxs[0]]; port_eoy = portfolio[yr_idxs[-1]]
        ann_ret  = (port_eoy - port_boy) / port_boy
        if yr < DD_START_YEAR:
            diag.append(dict(year=yr, port_boy=round(port_boy,2),
                             port_eoy=round(port_eoy,2),
                             ann_ret=round(ann_ret*100,2), passed=True)); continue
        passed = ann_ret >= -DD_LIMIT
        diag.append(dict(year=yr, port_boy=round(port_boy,2),
                         port_eoy=round(port_eoy,2),
                         ann_ret=round(ann_ret*100,2), passed=passed))
        if not passed:
            return False, ann_ret, diag
    worst = min(d["ann_ret"] for d in diag)
    return True, worst / 100, diag


def build_grid():
    return list(itertools.product(
        ENTRY_SIGNALS, DROP_LEVELS, EXIT_SIGNALS,
        BUY_PCTS, ALLOC_BASES, ALLOC_X2S
    ))


def run_search(df, grid):
    records = []
    for params in tqdm(grid, desc="Scanning combos"):
        entry, drop, exit_, buy, ab, ax2 = params
        cagr, port, nav = backtest(df, entry, drop, exit_, buy, ab, ax2)
        passed, abs_dd, _ = check_dd(df, port, nav)
        records.append({
            "entry_signal":  entry, "drop_level": drop, "exit_signal": exit_,
            "buy_pct": buy, "alloc_base": ab, "alloc_x2": ax2,
            "alloc_x3": round(1-ax2, 4), "cagr": round(cagr*100, 2),
            "worst_ann_ret": round(abs_dd*100, 2), "passed": passed,
        })
    return pd.DataFrame(records)


def plot_top_combos(df, results, n=5):
    top = results[results["passed"]].nlargest(n, "cagr")
    fig, ax = plt.subplots(figsize=(14, 7))
    bh = CAPITAL * df["SPY"] / df["SPY"].iloc[0]
    ax.plot(df.index, bh, label="SPY Buy & Hold",
            color="steelblue", linewidth=1.8, linestyle="--")
    colors = plt.cm.tab10(np.linspace(0, 0.7, n))
    for rank, (_, row) in enumerate(top.iterrows()):
        cagr, port, _ = backtest(df, row["entry_signal"], row["drop_level"],
                                 row["exit_signal"], row["buy_pct"],
                                 row["alloc_base"], row["alloc_x2"])
        label = (f"#{rank+1}  e={row['entry_signal']} d={row['drop_level']} "
                 f"x={row['exit_signal']} b={row['buy_pct']}  "
                 f"SPY={row['alloc_base']} SSO={row['alloc_x2']} "
                 f"UPRO={row['alloc_x3']}  "
                 f"CAGR={row['cagr']:.1f}% DD={row['worst_ann_ret']:.1f}%")
        ax.plot(df.index, port, label=label, color=colors[rank], linewidth=1.2)
    ax.set_title("Top combos — MA50 exit, SPY / SSO / UPRO", fontsize=11)
    ax.set_ylabel("Portfolio Value ($)")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax.legend(fontsize=7, loc="upper left")
    ax.grid(True, alpha=0.35)
    plt.tight_layout()
    plt.savefig(OUT_DIR / "ma50_exit_equity.png", dpi=150)
    print("  Saved: ma50_exit_equity.png")
    plt.show()


def plot_scatter(results):
    fig, ax = plt.subplots(figsize=(10, 6))
    fail  = results[~results["passed"]]
    pass_ = results[ results["passed"]]
    ax.scatter(fail["worst_ann_ret"],  fail["cagr"], alpha=0.15, s=6,
               color="tomato",  label="fail")
    ax.scatter(pass_["worst_ann_ret"], pass_["cagr"], alpha=0.4, s=6,
               color="seagreen", label="pass")
    ax.axvline(-DD_LIMIT*100, color="black", linestyle="--", linewidth=1,
               label=f"-{DD_LIMIT*100:.0f}% annual return cap")
    ax.set_xlabel("Worst Annual Return (%)")
    ax.set_ylabel("CAGR (%)")
    ax.set_title("CAGR vs Worst Annual Return — MA50 exit, SPY / SSO / UPRO")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(OUT_DIR / "ma50_exit_scatter.png", dpi=150)
    print("  Saved: ma50_exit_scatter.png")
    plt.show()


if __name__ == "__main__":
    df = load_data()
    print(f"\nData range  : {df.index[0].date()} -> {df.index[-1].date()}")
    print(f"Entry / arm : MA200 x entry_signal")
    print(f"Exit        : MA50 x exit_signal")
    print(f"Year-end DD cap : {DD_LIMIT*100:.0f}% max annual loss\n")

    grid = build_grid()
    print(f"Grid size   : {len(grid):,} combos\n")

    results = run_search(df, grid)
    results.to_csv(OUT_DIR / "ma50_exit_results.csv", index=False)
    print(f"\n  Saved: ma50_exit_results.csv  ({len(results):,} rows)")

    passing = results[results["passed"]].sort_values("cagr", ascending=False)
    print(f"  Passing combos: {len(passing):,} / {len(results):,}")

    print("\n" + "=" * 105)
    print("  LEADERBOARD — top 20 passing combos (MA50 exit), ranked by CAGR")
    print("=" * 105)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 120)
    print(passing.head(20).to_string(index=False))

    if not passing.empty:
        best = passing.iloc[0]
        print("\n" + "=" * 105)
        print("  BEST COMBO (MA50 exit)")
        print("=" * 105)
        print(f"  entry={best['entry_signal']}  drop={best['drop_level']}  "
              f"exit={best['exit_signal']}  buy={best['buy_pct']}")
        print(f"  SPY={best['alloc_base']*100:.0f}%  "
              f"SSO={best['alloc_x2']*100:.0f}%  "
              f"UPRO={best['alloc_x3']*100:.0f}%")
        print(f"  CAGR              : {best['cagr']:.2f}%")
        print(f"  Worst annual return : {best['worst_ann_ret']:.2f}%")

        cagr, port, nav = backtest(df, best["entry_signal"], best["drop_level"],
                                   best["exit_signal"], best["buy_pct"],
                                   best["alloc_base"], best["alloc_x2"])
        _, _, diag = check_dd(df, port, nav)
        if diag:
            print("\n  Year-end drawdown check:")
            for d in diag:
                flag = "OK" if d["passed"] else "FAIL"
                print(f"    {d['year']}  start ${d['port_boy']:,.0f}  "
                      f"end ${d['port_eoy']:,.0f}  "
                      f"return {d['ann_ret']:+.1f}%  {flag}")

    print("\n" + "=" * 105)
    print("  HEAD-TO-HEAD: MA200 exit vs MA50 exit on identical parameters")
    print("=" * 105)
    combos = []
    if not passing.empty:
        b = passing.iloc[0]
        combos.append((
            f"MA50-exit best   entry={b['entry_signal']} drop={b['drop_level']} "
            f"exit={b['exit_signal']} buy={b['buy_pct']} "
            f"SPY={b['alloc_base']} SSO={b['alloc_x2']} UPRO={b['alloc_x3']}",
            (b['entry_signal'], b['drop_level'], b['exit_signal'],
             b['buy_pct'], b['alloc_base'], b['alloc_x2'])
        ))
    combos += [
        ("MA200-baseline   entry=1.02 drop=0.005 exit=0.95 buy=0.3 SPY=0 SSO=0 UPRO=1",
         (1.02, 0.005, 0.95, 0.30, 0.0, 0.0)),
        ("MA200-balanced   entry=1.02 drop=0.010 exit=0.95 buy=0.4 SPY=0 SSO=1 UPRO=0",
         (1.02, 0.010, 0.95, 0.40, 0.0, 1.0)),
    ]
    for label, p in combos:
        entry, drop, exit_, buy, ab, ax2 = p
        c50,  port50,  nav50  = backtest(df, entry, drop, exit_, buy, ab, ax2)
        c200, port200, nav200 = backtest_ma200_exit(df, entry, drop, exit_, buy, ab, ax2)
        ok50,  dd50,  _ = check_dd(df, port50,  nav50)
        ok200, dd200, _ = check_dd(df, port200, nav200)
        print(f"\n  {label}")
        print(f"    MA50  exit : CAGR {c50*100:.2f}%  worst {dd50*100:.2f}%  "
              f"{'PASS' if ok50 else 'FAIL'}")
        print(f"    MA200 exit : CAGR {c200*100:.2f}%  worst {dd200*100:.2f}%  "
              f"{'PASS' if ok200 else 'FAIL'}")

    print("\nPlotting ...")
    plot_top_combos(df, results, n=5)
    plot_scatter(results)
