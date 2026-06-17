"""
EXPERIMENT (sandbox — does NOT touch the project) — regime reliability of the
MA200 / MA50 trend signal.

Question: "Can we rely on MA200 / MA50?" The production strategy is optimized on
2003+ only, which contains no multi-year SIDEWAYS secular bear. This script
stress-tests the *core signal* (not the leverage tuning) on ~75 years of S&P 500
(^GSPC) — data entirely outside the optimization window — and reports how the
trend rule behaves in each secular regime, especially the 1966–1982 chop that
Meta AI flagged.

We test the SIGNAL in isolation (unleveraged long/cash timing) so the result
is about the moving average itself, then add a 3x synthetic overlay to show how
leverage interacts with whipsaws in a sideways market.

Nothing here imports the project; it is a clean, self-contained re-derivation.
"""

import numpy as np
import pandas as pd
import yfinance as yf

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)

SECULAR_REGIMES = [
    ("1950-1966 secular bull",     "1950-01-01", "1966-02-01"),
    ("1966-1982 sideways/chop",    "1966-02-01", "1982-08-01"),
    ("1982-2000 secular bull",     "1982-08-01", "2000-03-24"),
    ("2000-2009 lost decade",      "2000-03-24", "2009-03-09"),
    ("2009-2026 secular bull",     "2009-03-09", "2026-06-11"),
]


def cagr(curve: pd.Series) -> float:
    yrs = (curve.index[-1] - curve.index[0]).days / 365.25
    return (curve.iloc[-1] / curve.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else 0.0


def max_dd(curve: pd.Series) -> float:
    return ((curve / curve.cummax()) - 1).min()


def lev_daily(ret: pd.Series, L: int) -> pd.Series:
    """Daily-reset L x leveraged return with the standard variance-decay term."""
    var20 = ret.rolling(20).var().fillna(0)
    return L * ret - 0.5 * (L**2 - L) * var20


def timing_curve(px, ret, ma, mult_lev=1, cost=0.0005):
    """Long when yesterday's close > MA (×1.0), else cash. mult_lev>1 applies a
    daily-reset leveraged return while in-market. Returns (equity, signal)."""
    in_mkt = (px > ma).shift(1).fillna(False)          # 1-day lag, no lookahead
    if mult_lev == 1:
        day_ret = ret.where(in_mkt, 0.0)
    else:
        day_ret = lev_daily(ret, mult_lev).where(in_mkt, 0.0)
    # transaction cost on every state change (enter or exit)
    switches = in_mkt.astype(int).diff().abs().fillna(0)
    day_ret = day_ret - switches * cost
    return (1 + day_ret).cumprod(), in_mkt


def roundtrip_stats(px, in_mkt):
    """Count buy->sell round trips and the fraction that lost money (whipsaws)."""
    sig = in_mkt.astype(int)
    chg = sig.diff().fillna(0)
    entries = px.index[chg == 1]
    exits   = px.index[chg == -1]
    n = min(len(entries), len(exits))
    rets = []
    for i in range(n):
        e, x = entries[i], exits[i]
        if x > e:
            rets.append(px.loc[x] / px.loc[e] - 1)
    rets = np.array(rets)
    if rets.size == 0:
        return 0, np.nan, np.nan
    return len(rets), float((rets < 0).mean()), float(rets.mean())


def regime_row(name, sl_px, sl_ret, ma_full, label, lev=1):
    ma = ma_full.reindex(sl_px.index)
    eq, in_mkt = timing_curve(sl_px, sl_ret, ma, mult_lev=lev)
    bh = (1 + sl_ret).cumprod()
    n_rt, whip, avg_rt = roundtrip_stats(sl_px, in_mkt)
    return {
        "regime": name,
        "rule": label,
        "B&H CAGR": f"{cagr(bh)*100:6.1f}%",
        "rule CAGR": f"{cagr(eq)*100:6.1f}%",
        "edge pp": f"{(cagr(eq)-cagr(bh))*100:+6.1f}",
        "B&H maxDD": f"{max_dd(bh)*100:6.0f}%",
        "rule maxDD": f"{max_dd(eq)*100:6.0f}%",
        "in-mkt": f"{in_mkt.mean()*100:4.0f}%",
        "roundtrips": n_rt,
        "whipsaw%": ("  n/a" if np.isnan(whip) else f"{whip*100:4.0f}%"),
        "avg trip": ("  n/a" if np.isnan(avg_rt) else f"{avg_rt*100:+5.1f}%"),
    }


def main():
    print("Downloading ^GSPC (S&P 500) full history from yfinance …")
    raw = yf.download("^GSPC", start="1949-01-01", end="2026-06-12",
                      auto_adjust=True, progress=False)
    px = raw["Close"].squeeze().dropna()
    px.index = pd.to_datetime(px.index)
    print(f"  Got {len(px):,} daily closes: {px.index[0].date()} -> {px.index[-1].date()}\n")

    ret   = px.pct_change().fillna(0)
    ma200 = px.rolling(200).mean()
    ma50  = px.rolling(50).mean()

    for lev, tag in [(1, "UNLEVERAGED signal (long/cash)"),
                     (3, "3x SYNTHETIC overlay (leverage + whipsaw interaction)")]:
        print("=" * 150)
        print(f"  {tag}")
        print("=" * 150)
        for ma_full, label in [(ma200, "MA200"), (ma50, "MA50")]:
            rows = []
            for name, a, b in SECULAR_REGIMES:
                sl_px  = px.loc[a:b]
                sl_ret = ret.loc[a:b]
                if len(sl_px) < 250:
                    continue
                rows.append(regime_row(name, sl_px, sl_ret, ma_full, label, lev=lev))
            # full sample
            rows.append(regime_row("FULL 1950-2026", px, ret, ma_full, label, lev=lev))
            print(pd.DataFrame(rows).to_string(index=False))
            print()


if __name__ == "__main__":
    main()
