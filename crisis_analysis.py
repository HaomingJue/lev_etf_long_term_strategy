"""
crisis_analysis.py — two deliverables in one pass (data downloaded once per index):

  1. TRADE STATISTICS (README §1 / daily_signal) — how often each production
     variant actually trades. Counts buy-executions and exits (round-trips) over
     full history, and reports trades-per-year + the busiest single year. The
     story: the strategy is overwhelmingly HOLD; you act ~twice a year.

  2. CRISIS TAIL-RISK FIGURES (README §8) — for each historical crisis, an
     equity panel (normalized to 100 at the crisis start) and an underwater
     (drawdown) panel, overlaying all three QQQ variants + SPY Balanced against
     buy-and-hold, so the section can analyze each crisis with diagrams instead
     of a bare table.

The backtest engine is a trade-counting copy of optimizer_core.opt_backtest;
its "Total trades" cross-checks the authoritative backtester.py
(QQQ Aggressive 43, Balanced 50, Conservative 47, SPY Balanced 59).

Run:  python crisis_analysis.py            # writes results/crisis/*
"""
from __future__ import annotations

import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yfinance as yf

import optimizer_core as oc

warnings.filterwarnings("ignore")

OUT = Path(__file__).parent / "results" / "crisis"
OUT.mkdir(parents=True, exist_ok=True)

# Download from here so the dot-com window (2000-2003) has warm MA200s. The
# leveraged series is 100% synthetic before real-ETF inception (disclosed).
LOAD_START = "1998-01-01"
LOAD_END   = oc.DEFAULT_END

# ── Production variants (full-history param sets; match README §4 / §8) ──────
VARIANTS = [
    dict(key="QQQ_aggr", preset="QQQ", label="QQQ Aggressive — Max-CAGR (3×, buy 100%)",
         entry=1.04, drop=0.0, exit=1.01, buy=1.0, base=0.0, x2=0.0, ma=200,
         color="#c0392b", lw=2.2),
    dict(key="QQQ_bal", preset="QQQ", label="QQQ Balanced — DD-Capped (3×, buy 90% + 10% base)",
         entry=1.04, drop=0.0, exit=1.01, buy=0.9, base=0.1, x2=0.0, ma=200,
         color="#e67e22", lw=2.0),
    dict(key="QQQ_cons", preset="QQQ", label="QQQ Conservative — Calmar (2×, buy 80% + 20% base)",
         entry=1.04, drop=0.0, exit=1.01, buy=0.8, base=0.2, x2=1.0, ma=200,
         color="#27ae60", lw=2.0),
    dict(key="SPY_bal", preset="SPY", label="SPY Balanced — Buy-Capped (3× buy 20%, MA50)",
         entry=1.02, drop=0.0025, exit=0.93, buy=0.2, base=0.0, x2=0.0, ma=50,
         color="#2980b9", lw=2.0),
]

CRISES = [
    dict(key="dotcom", start="2000-01-01", end="2003-12-31",
         title="Dot-com crash & aftermath (2000–2003)"),
    dict(key="gfc", start="2007-06-01", end="2009-12-31",
         title="Global Financial Crisis (2007–2009)"),
    dict(key="covid", start="2020-01-01", end="2020-12-31",
         title="COVID crash & V-recovery (2020)"),
    dict(key="ratehike", start="2021-11-01", end="2023-06-30",
         title="2022 rate-hike bear (Nov 2021–mid 2023)"),
]


# ── Data ────────────────────────────────────────────────────────────────────
def load(preset: str) -> pd.DataFrame:
    cfg = oc.PRESETS[preset]
    print(f"Downloading {preset} set ({cfg['base']}, {cfg['lev2']}, {cfg['lev3']}) …")

    def dl(tk):
        s = yf.download(tk, start=LOAD_START, end=LOAD_END,
                        auto_adjust=True, progress=False)["Close"].squeeze().dropna()
        s.name = tk
        return s

    base = dl(cfg["base"])
    df = pd.DataFrame({
        "base": base,
        "lev2": oc._build_lev_nav(base, dl(cfg["lev2"]), 2, annual_mer=oc._MER_2X[preset]),
        "lev3": oc._build_lev_nav(base, dl(cfg["lev3"]), 3, annual_mer=oc._MER_3X[preset]),
    }).dropna(subset=["base"])
    df["MA50"]  = df["base"].rolling(50).mean()
    df["MA100"] = df["base"].rolling(100).mean()
    df["MA200"] = df["base"].rolling(200).mean()
    return df


# ── Trade-counting engine (mirrors optimizer_core.opt_backtest) ─────────────
def run(df: pd.DataFrame, v: dict):
    """Returns (port, n_buys, n_exits, buy_years, exit_years).
    n_buys  = days a leveraged/base purchase executed (one action/day).
    n_exits = days the trend-break exit fired with positions (= round-trips)."""
    entry, drop, exit_, buy, ab, ax2, exit_ma = (
        v["entry"], v["drop"], v["exit"], v["buy"], v["base"], v["x2"], v["ma"])
    ax3 = 1.0 - ax2
    eff_buy = min(buy, max(1.0 - ab, 0.0))   # lev buy capped at (1 - base)
    f = df.iloc[0]
    nb = df["base"].values / f["base"]
    n2 = df["lev2"].values / f["lev2"]
    n3 = df["lev3"].values / f["lev3"]
    ma_arm  = df["MA200"].values / f["base"]
    ma_exit = df[f"MA{exit_ma}"].values / f["base"]
    years   = df.index.year.values

    cash = oc.CAPITAL
    s_b = s_2 = s_3 = 0.0
    armed = False
    port = np.empty(len(df)); port[0] = oc.CAPITAL
    n_buys = n_exits = 0
    buy_years, exit_years = [], []

    for i in range(1, len(df)):
        if (np.isnan(ma_arm[i]) or ma_arm[i] == 0
                or np.isnan(ma_exit[i]) or ma_exit[i] == 0):
            port[i] = port[i-1]; continue
        v2 = s_2*n2[i]; v3 = s_3*n3[i]
        if nb[i] < ma_exit[i]*exit_ and (s_2 > 0 or s_3 > 0):
            cash += v2 + v3; s_2 = s_3 = 0.0
            n_exits += 1; exit_years.append(years[i])
            if ab > 0:                       # trim base back down to target
                vb = s_b*nb[i]; tgt = (cash+vb)*ab
                if vb > tgt+0.01:
                    s_b -= (vb-tgt)/nb[i]; cash += vb-tgt
            armed = False
        else:
            if not armed and nb[i] > ma_arm[i]*entry:
                armed = True
            d = (nb[i-1]-nb[i])/nb[i-1] if nb[i-1] > 0 else 0.0
            if armed and nb[i] > ma_arm[i]*entry and d >= drop and cash > 0.01:
                tot = cash + s_b*nb[i] + s_2*n2[i] + s_3*n3[i]
                did_buy = False
                if ab > 0:                   # top base up to target first
                    sp = min(max(tot*ab - s_b*nb[i], 0), cash)
                    if sp > 0.01:
                        s_b += sp/nb[i]; cash -= sp; did_buy = True
                    tot = cash + s_b*nb[i] + s_2*n2[i] + s_3*n3[i]
                lev = min(eff_buy*tot, cash)
                if lev > 0.01:
                    if ax2 > 0: s_2 += lev*ax2/n2[i]
                    if ax3 > 0: s_3 += lev*ax3/n3[i]
                    cash -= lev; did_buy = True
                if did_buy:
                    n_buys += 1; buy_years.append(years[i])
        port[i] = cash + s_b*nb[i] + s_2*n2[i] + s_3*n3[i]
    return port, n_buys, n_exits, buy_years, exit_years


def cagr(port, idx):
    days = (idx[-1]-idx[0]).days
    return (port[-1]/port[0])**(365.25/days)-1 if days > 0 else 0.0


def maxdd(port):
    rm = np.maximum.accumulate(port)
    return float(((port-rm)/rm).min())


# ── 1. TRADE STATISTICS (full history) ──────────────────────────────────────
def trade_stats(data: dict) -> str:
    lines = ["TRADE FREQUENCY — full history (2003-01-01 → data end)\n",
             f"{'Variant':<42}{'yrs':>5}{'buys':>6}{'exits':>7}"
             f"{'trades':>8}{'tr/yr':>7}{'busiest yr':>22}"]
    for v in VARIANTS:
        df = data[v["preset"]]
        df = df[df.index >= oc.START_DATE]
        port, nb, nx, by, xy = run(df, v)
        yrs = (df.index[-1]-df.index[0]).days/365.25
        total = nb + nx
        # busiest single calendar year (buys+exits)
        allacts = pd.Series(by+xy)
        if len(allacts):
            vc = allacts.value_counts()
            busiest = f"{int(vc.index[0])} ({int(vc.iloc[0])} trades)"
        else:
            busiest = "—"
        lines.append(f"{v['label']:<42}{yrs:>5.1f}{nb:>6}{nx:>7}"
                     f"{total:>8}{total/yrs:>7.1f}{busiest:>22}")
    txt = "\n".join(lines) + "\n"
    (OUT/"trade_stats.txt").write_text(txt, encoding="utf-8")
    print("\n"+txt)
    return txt


# ── 2. CRISIS FIGURES + STATS ───────────────────────────────────────────────
def crisis_figures(data: dict) -> str:
    rows = []
    for cr in CRISES:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 8.5),
                                       gridspec_kw={"height_ratios": [2, 1]},
                                       sharex=True)
        for v in VARIANTS:
            df = data[v["preset"]]
            win = df[(df.index >= cr["start"]) & (df.index <= cr["end"])]
            if len(win) < 30:
                continue
            port, nb, nx, _, _ = run(win, v)
            norm = 100.0 * port / port[0]
            dd = (port/np.maximum.accumulate(port) - 1.0)*100
            ax1.plot(win.index, norm, color=v["color"], lw=v["lw"], label=v["label"])
            ax2.plot(win.index, dd, color=v["color"], lw=v["lw"])
            rows.append((cr["title"], v["label"],
                         (port[-1]/port[0]-1)*100, maxdd(port)*100, nb+nx))
        # buy & hold references (base index, normalized) + stats
        for preset, c in (("QQQ", "#7f8c8d"), ("SPY", "#bdc3c7")):
            df = data[preset]
            win = df[(df.index >= cr["start"]) & (df.index <= cr["end"])]
            if len(win) < 30:
                continue
            base = win["base"].values
            bh = 100.0*base/base[0]
            ax1.plot(win.index, bh, color=c, lw=1.3, ls="--",
                     label=f"{preset} buy & hold (1×)")
            rows.append((cr["title"], f"{preset} buy & hold (1×)",
                         (base[-1]/base[0]-1)*100, maxdd(base)*100, 0))
        # leveraged buy & hold (hold the 3x ETF straight through — no timing
        # rule). Isolates the MA timing from the leverage. Stats only; plotting
        # it would crush the chart's y-scale (it can fall ~99%).
        for preset, etf in (("QQQ", "TQQQ"), ("SPY", "UPRO")):
            df = data[preset]
            win = df[(df.index >= cr["start"]) & (df.index <= cr["end"])]
            if len(win) < 30:
                continue
            l3 = win["lev3"].values
            rows.append((cr["title"], f"{preset} hold {etf} (3×, no timing)",
                         (l3[-1]/l3[0]-1)*100, maxdd(l3)*100, 0))
        ax1.set_yscale("log")
        ax1.set_title(f"{cr['title']} — equity (start = 100, log scale)")
        ax1.set_ylabel("Value (start = 100)")
        ax1.legend(fontsize=8, loc="best"); ax1.grid(True, alpha=0.3)
        ax2.set_title("Drawdown (underwater curve)")
        ax2.set_ylabel("% below prior peak"); ax2.grid(True, alpha=0.3)
        ax2.axhline(0, color="k", lw=0.6)
        fig.tight_layout()
        p = OUT/f"crisis_{cr['key']}.png"
        fig.savefig(p, dpi=110); plt.close(fig)
        print(f"saved {p}")

    tbl = pd.DataFrame(rows, columns=["crisis", "variant", "period_ret_%",
                                      "maxDD_%", "trades"])
    tbl["period_ret_%"] = tbl["period_ret_%"].round(1)
    tbl["maxDD_%"] = tbl["maxDD_%"].round(1)
    txt = tbl.to_string(index=False)
    (OUT/"crisis_stats.txt").write_text(txt, encoding="utf-8")
    print("\n"+txt)
    return txt


if __name__ == "__main__":
    data = {p: load(p) for p in ("QQQ", "SPY")}
    trade_stats(data)
    crisis_figures(data)
    print("\nDONE → results/crisis/")
