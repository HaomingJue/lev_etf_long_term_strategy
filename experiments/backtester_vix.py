"""
EXPERIMENT (sandbox — does NOT modify the project) — VIX regime filter on the
leveraged strategy.

This is a COPY of the strategy engine (faithfully mirrors
optimizer_core.opt_backtest) with a VIX gate bolted on, so the user's idea can
be tested without touching any shipped file. Data loading + synthetic NAVs are
imported read-only from optimizer_core; nothing there is changed.

VIX gate (the decision on day i uses day i-1's VIX close — no lookahead):
  mode "exit"  : when VIX > vix_max -> sell ALL leverage to cash AND block new
                 buys (the aggressive "risk-off to cash" filter).
  mode "block" : when VIX > vix_max -> only block NEW buys, keep existing
                 leverage (gentler — refuse to buy into a vol spike, but don't
                 panic-sell what you hold).

With vix_max=None the engine is identical to opt_backtest, so the no-VIX row is
a validation of the baseline against the README's published numbers.

Run:  python experiments/backtester_vix.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from optimizer_core import load_full_data, max_drawdown, CAPITAL


def load_vix(index, end="2026-06-12"):
    v = yf.download("^VIX", start="1990-01-01", end=end,
                    auto_adjust=True, progress=False)["Close"].squeeze().dropna()
    v.index = pd.to_datetime(v.index)
    return v.reindex(index, method="ffill")


def vix_backtest(df, vix_prev, entry, drop, exit_, buy, ab, ax2,
                 exit_ma=200, vix_max=None, mode="exit"):
    """Mirror of optimizer_core.opt_backtest + a VIX gate. Returns a metrics dict."""
    ax3 = 1.0 - ax2
    eff_buy = min(buy, max(1.0 - ab, 0.0))
    f = df.iloc[0]
    nb = df["base"].values / f["base"]
    n2 = df["lev2"].values / f["lev2"]
    n3 = df["lev3"].values / f["lev3"]
    ma_arm  = df["MA200"].values / f["base"]
    ma_exit = df[f"MA{exit_ma}"].values / f["base"]
    vx = vix_prev.values

    cash = CAPITAL; s_b = s_2 = s_3 = 0.0; armed = False
    port = np.empty(len(df)); port[0] = CAPITAL
    n_exit = n_forced = n_buy = 0

    for i in range(1, len(df)):
        if (np.isnan(ma_arm[i]) or ma_arm[i] == 0
                or np.isnan(ma_exit[i]) or ma_exit[i] == 0):
            port[i] = port[i-1]; continue

        risk_off = (vix_max is not None and not np.isnan(vx[i]) and vx[i] > vix_max)
        force_exit = risk_off and mode == "exit"

        v2 = s_2 * n2[i]; v3 = s_3 * n3[i]

        if ((nb[i] < ma_exit[i] * exit_) or force_exit) and (s_2 > 0 or s_3 > 0):
            forced_only = force_exit and nb[i] >= ma_exit[i] * exit_
            cash += v2 + v3; s_2 = s_3 = 0.0
            if ab > 0:
                vb = s_b * nb[i]; tgt = (cash + vb) * ab
                if vb > tgt + 0.01:
                    s_b -= (vb - tgt) / nb[i]; cash += vb - tgt
            armed = False
            n_exit += 1
            n_forced += int(forced_only)
        else:
            if not armed and nb[i] > ma_arm[i] * entry:
                armed = True
            d = (nb[i-1] - nb[i]) / nb[i-1] if nb[i-1] > 0 else 0.0
            if (armed and nb[i] > ma_arm[i] * entry and d >= drop
                    and cash > 0.01 and not risk_off):
                tot = cash + s_b*nb[i] + s_2*n2[i] + s_3*n3[i]
                if ab > 0:
                    sp = min(max(tot*ab - s_b*nb[i], 0), cash)
                    if sp > 0.01:
                        s_b += sp/nb[i]; cash -= sp
                    tot = cash + s_b*nb[i] + s_2*n2[i] + s_3*n3[i]
                lev = min(eff_buy*tot, cash)
                if lev > 0.01:
                    if ax2 > 0: s_2 += lev*ax2/n2[i]
                    if ax3 > 0: s_3 += lev*ax3/n3[i]
                    cash -= lev; n_buy += 1

        port[i] = cash + s_b*nb[i] + s_2*n2[i] + s_3*n3[i]

    days = (df.index[-1] - df.index[0]).days
    cg = (port[-1]/CAPITAL)**(365.25/days) - 1 if days > 0 else 0.0
    yrs = df.index.year.to_numpy(); worst = 0.0
    for y in np.unique(yrs):
        m = np.where(yrs == y)[0]
        worst = min(worst, (port[m[-1]] - port[m[0]]) / port[m[0]])
    return dict(cagr=cg*100, maxdd=max_drawdown(port)*100, worst=worst*100,
                final=port[-1], n_exit=n_exit, n_forced=n_forced, n_buy=n_buy)


# QQQ Balanced (DD-Capped) — the recommended shipped config.
CFG = dict(entry=1.04, drop=0.0, exit_=1.01, buy=0.9, ab=0.1, ax2=0.0, exit_ma=200)
THRESHOLDS = [35, 30, 25, 20]
MODES = ["exit", "block"]


def sweep(df, vix_prev, label):
    print("\n" + "=" * 104)
    print(f"  {label}   (QQQ Balanced DD-Capped: entry 1.04 / drop 0 / exit 1.01 / "
          f"buy 90% + 10% base / MA200)")
    print("=" * 104)
    hdr = f"  {'VIX gate':<22} {'CAGR':>7} {'maxDD':>8} {'worst yr':>9} {'final $':>12} {'forced exits':>13}"
    print(hdr); print("  " + "-" * 100)
    base = vix_backtest(df, vix_prev, **CFG, vix_max=None)
    print(f"  {'none (baseline)':<22} {base['cagr']:>6.1f}% {base['maxdd']:>7.1f}% "
          f"{base['worst']:>8.1f}% {base['final']:>12,.0f} {'-':>13}")
    for mode in MODES:
        for thr in THRESHOLDS:
            r = vix_backtest(df, vix_prev, **CFG, vix_max=thr, mode=mode)
            print(f"  {f'VIX>{thr} -> {mode}':<22} {r['cagr']:>6.1f}% {r['maxdd']:>7.1f}% "
                  f"{r['worst']:>8.1f}% {r['final']:>12,.0f} {r['n_forced']:>13}")


def main():
    df = load_full_data("QQQ")
    vix_prev = load_vix(df.index).shift(1)
    sweep(df, vix_prev, "FULL HISTORY 2003-2026")
    df15 = df[df.index.year >= 2015]
    sweep(df15, vix_prev.reindex(df15.index), "OUT-OF-SAMPLE WINDOW 2015-2026")


if __name__ == "__main__":
    main()
