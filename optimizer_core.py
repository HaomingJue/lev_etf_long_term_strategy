"""
optimizer_core.py — single source of truth for the grid search.

Everything that both the standalone optimizer (optimizer.py) and the
walk-forward runner (walkforward.py) need lives here, so the two tools can
never drift apart again:

  - presets (tickers, DD-filter start year)
  - data loading with synthetic leveraged NAV + MER correction
  - the strategy backtest engine used for grid scanning
  - the calendar-year drawdown filter
  - the parameter grids (v1 / v2 / v3) and grid builder
  - a parallel grid-search runner that returns one row per combo

History of the grids (see README):
  v1 — original study grid (15,840 combos).
  v2 — 2026-06 extension after v1 winners pinned at drop_level=0.005 min and
       buy_pct=0.40 max (31,680 combos).
  v3 — 2026-06 full refresh. v2 winners pinned again at buy_pct=0.60, and
       SPY pinned at the exit_signal=0.95 floor (the shifted-grid sweep showed
       the working band extends to 0.93). v3 extends buy_pct to 1.00, exits
       down to 0.93, probes negative drop levels (buy even on mildly up days),
       and prunes the dead 2.5%/3.0% drops (72,000 combos).
"""

import itertools
import warnings
from multiprocessing import Pool

import numpy as np
import pandas as pd
import yfinance as yf
from tqdm import tqdm

warnings.filterwarnings("ignore")

# ──────────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────────

PRESETS = {
    "QQQ": {"base": "QQQ", "lev2": "QLD", "lev3": "TQQQ", "dd_start": 2010},
    "SPY": {"base": "SPY", "lev2": "SSO", "lev3": "UPRO", "dd_start": 2009},
    "IWM": {"base": "IWM", "lev2": "UWM", "lev3": "TNA",  "dd_start": 2009},
}

START_DATE   = "2003-01-01"
WARMUP_START = "2001-01-01"   # download start — MA200 fully warm by START_DATE
DEFAULT_END  = "2026-06-12"   # yfinance end-exclusive → data through 2026-06-11
CAPITAL      = 10_000
DD_LIMIT     = 0.40           # calendar-year loss cap for the pass/fail filter

# Annual MERs — applied to the synthetic pre-inception period only
# (real ETF prices already embed the fund's expenses).
_MER_3X = {"QQQ": 0.0095, "SPY": 0.0091, "IWM": 0.0109}
_MER_2X = {"QQQ": 0.0095, "SPY": 0.0089, "IWM": 0.0095}


# ──────────────────────────────────────────────────────────────
# PARAMETER GRIDS
# ──────────────────────────────────────────────────────────────

GRID_AXES = {
    "v1": dict(
        entry=[1.01, 1.02, 1.03, 1.04, 1.05, 1.06],
        drop=[0.005, 0.010, 0.015, 0.020, 0.025, 0.030],
        exit=[0.95, 0.97, 0.99, 1.00, 1.01, 1.02],
        buy=[0.10, 0.20, 0.30, 0.40],
        base=[0.0, 0.10, 0.20, 0.30],
        x2=[0.0, 0.25, 0.50, 0.75, 1.0],
    ),
    "v2": dict(
        entry=[1.01, 1.02, 1.03, 1.04, 1.05, 1.06],
        drop=[0.0, 0.0025, 0.005, 0.010, 0.015, 0.020, 0.025, 0.030],
        exit=[0.95, 0.97, 0.99, 1.00, 1.01, 1.02],
        buy=[0.10, 0.20, 0.30, 0.40, 0.50, 0.60],
        base=[0.0, 0.10, 0.20, 0.30],
        x2=[0.0, 0.25, 0.50, 0.75, 1.0],
    ),
    "v3": dict(
        # drop < 0 means: buy even on a day that closed UP by less than |drop|.
        # drop = -0.01 ≈ buy on any day that didn't rally more than 1%.
        entry=[1.01, 1.02, 1.03, 1.04, 1.05, 1.06],
        drop=[-0.010, -0.005, 0.0, 0.0025, 0.005, 0.010, 0.015, 0.020],
        exit=[0.93, 0.94, 0.95, 0.97, 0.99, 1.00, 1.01, 1.02],
        buy=[0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.00],
        base=[0.0, 0.10, 0.20, 0.30],
        x2=[0.0, 0.25, 0.50, 0.75, 1.0],
    ),
    # v3cap: identical to v3 but buy_pct capped at 0.60. Investigates whether
    # SPY's out-of-sample walk-forward failure is caused purely by the
    # optimizer selecting aggressive (buy 80-100%) position sizes that pass the
    # in-sample DD filter but breach it badly OOS (e.g. -49% SPY 2022).
    "v3cap": dict(
        entry=[1.01, 1.02, 1.03, 1.04, 1.05, 1.06],
        drop=[-0.010, -0.005, 0.0, 0.0025, 0.005, 0.010, 0.015, 0.020],
        exit=[0.93, 0.94, 0.95, 0.97, 0.99, 1.00, 1.01, 1.02],
        buy=[0.10, 0.20, 0.30, 0.40, 0.50, 0.60],
        base=[0.0, 0.10, 0.20, 0.30],
        x2=[0.0, 0.25, 0.50, 0.75, 1.0],
    ),
}

DEFAULT_GRID = "v3"


def build_grid(grid_version: str = DEFAULT_GRID):
    """All valid (entry, drop, exit, buy, alloc_base, alloc_x2) combos.

    Constraint: exit_signal < entry_signal — you can't place the exit
    threshold above the level you armed at.
    """
    ax = GRID_AXES[grid_version]
    return [(e, d, x, b, ab, ax2)
            for e, d, x, b, ab, ax2 in itertools.product(
                ax["entry"], ax["drop"], ax["exit"],
                ax["buy"], ax["base"], ax["x2"])
            if x < e]


# ──────────────────────────────────────────────────────────────
# DATA
# ──────────────────────────────────────────────────────────────

def _build_lev_nav(base: pd.Series, real: pd.Series, L: int,
                   annual_mer: float = 0.0) -> pd.Series:
    """Synthetic L× NAV before real-ETF inception, stitched to real prices.

    lev_daily_ret = L·r − 0.5·(L²−L)·var20 − MER/252 (MER synthetic-only).
    """
    ret       = base.pct_change().fillna(0)
    var20     = ret.rolling(20).var().fillna(0)
    daily_mer = annual_mer / 252.0

    first_real_pos = None
    if real is not None and not real.dropna().empty:
        common = base.index.intersection(real.dropna().index)
        if not common.empty:
            first_real_pos = base.index.get_loc(common[0])

    nav = np.ones(len(base))
    for i in range(1, len(base)):
        lev_r = L * ret.values[i] - 0.5 * (L**2 - L) * var20.values[i]
        if first_real_pos is None or i < first_real_pos:
            lev_r -= daily_mer
        nav[i] = nav[i-1] * (1.0 + lev_r)

    synth = pd.Series(nav, index=base.index)
    if first_real_pos is None:
        return synth
    first    = base.index[first_real_pos]
    stitched = synth.copy()
    stitched.loc[first:] = (real.reindex(base.index).loc[first:]
                            * (synth.loc[first] / real.loc[first]))
    return stitched


def load_full_data(preset: str, end: str = DEFAULT_END) -> pd.DataFrame:
    """Base + stitched 2×/3× NAVs with MA50/100/200, from START_DATE."""
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
    df["MA50"]  = df["base"].rolling(50).mean()
    df["MA100"] = df["base"].rolling(100).mean()
    df["MA200"] = df["base"].rolling(200).mean()
    df = df[df.index >= START_DATE].copy()
    return df


# ──────────────────────────────────────────────────────────────
# BACKTEST ENGINE (grid-scan precision; backtester.py is the
# authoritative single-run tool with trade logs / costs / taxes)
# ──────────────────────────────────────────────────────────────

def opt_backtest(df: pd.DataFrame, entry, drop, exit_, buy, ab, ax2,
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


def max_drawdown(port: np.ndarray) -> float:
    """Worst peak-to-trough drop of the equity curve, as a negative fraction
    (e.g. −0.55 = −55%). Used for the Calmar (CAGR / |maxDD|) selection rule."""
    running_max = np.maximum.accumulate(port)
    return float(((port - running_max) / running_max).min())


def check_dd(df: pd.DataFrame, port: np.ndarray, dd_start: int,
             dd_limit: float = DD_LIMIT, max_dd_limit: float = 1.0):
    """Pass = (a) no calendar year from dd_start onward lost more than dd_limit,
    and (b) the peak-to-trough max drawdown over the dd_start-onward (real-ETF)
    period does not exceed max_dd_limit (default 1.0 = no maxDD cap).

    The maxDD cap is measured only on the real-data period for the same reason
    as the annual filter: synthetic pre-inception drawdowns are too punishing.

    Returns (passed, worst_calendar_year_return) — worst is over ALL years,
    including pre-dd_start synthetic ones, for reporting.
    """
    idx   = df.index
    worst = 0.0
    for yr in np.unique(idx.year):
        mask = np.where(idx.year == yr)[0]
        ann  = (port[mask[-1]] - port[mask[0]]) / port[mask[0]]
        worst = min(worst, ann)
        if yr >= dd_start and ann < -dd_limit:
            return False, ann
    if max_dd_limit < 1.0:
        real = port[idx.year >= dd_start]
        if real.size:
            rm  = np.maximum.accumulate(real)
            mdd = ((real - rm) / rm).min()
            if mdd < -max_dd_limit:
                return False, worst
    return True, worst


# ──────────────────────────────────────────────────────────────
# PARALLEL GRID SEARCH
# ──────────────────────────────────────────────────────────────
# Embarrassingly parallel; each worker receives the training DataFrame once
# via the Pool initializer. pool.imap (ordered) keeps results in grid order
# so ties resolve identically to a single-worker run.

_W_DF = _W_EXIT_MA = _W_DD_START = _W_DD_LIMIT = _W_MAX_DD = None


def _init_worker(df, exit_ma, dd_start, dd_limit, max_dd_limit):
    global _W_DF, _W_EXIT_MA, _W_DD_START, _W_DD_LIMIT, _W_MAX_DD
    _W_DF, _W_EXIT_MA, _W_DD_START = df, exit_ma, dd_start
    _W_DD_LIMIT, _W_MAX_DD = dd_limit, max_dd_limit


def _eval_combo(combo):
    entry, drop, exit_, buy, ab, ax2 = combo
    c, port   = opt_backtest(_W_DF, entry, drop, exit_, buy, ab, ax2,
                             exit_ma=_W_EXIT_MA)
    ok, worst = check_dd(_W_DF, port, _W_DD_START, _W_DD_LIMIT, _W_MAX_DD)
    return ok, c, worst, max_drawdown(port), combo


def run_grid_search(df: pd.DataFrame, grid, exit_ma: int, dd_start: int,
                    workers: int = 1, desc: str = "grid",
                    dd_limit: float = DD_LIMIT,
                    max_dd_limit: float = 1.0) -> pd.DataFrame:
    """Run every combo; return one row per combo (pass and fail alike).

    Columns: entry_signal, drop_level, exit_signal, buy_pct, alloc_base,
    alloc_x2, alloc_x3, cagr (%), worst_ann_ret (%), passed.
    Rows keep grid order, so `df.loc[df[df.passed].cagr.idxmax()]` resolves
    ties identically across runs and worker counts.

    dd_limit : calendar-year loss cap for the pass/fail filter (default −40%).
    """
    records = []
    if workers > 1:
        with Pool(workers, initializer=_init_worker,
                  initargs=(df, exit_ma, dd_start, dd_limit, max_dd_limit)) as pool:
            for ok, c, worst, mdd, combo in tqdm(
                    pool.imap(_eval_combo, grid, chunksize=64),
                    total=len(grid), desc=desc, leave=False):
                records.append((combo, c, worst, mdd, ok))
    else:
        for combo in tqdm(grid, desc=desc, leave=False):
            entry, drop, exit_, buy, ab, ax2 = combo
            c, port   = opt_backtest(df, entry, drop, exit_, buy, ab, ax2,
                                     exit_ma=exit_ma)
            ok, worst = check_dd(df, port, dd_start, dd_limit, max_dd_limit)
            records.append((combo, c, worst, max_drawdown(port), ok))

    out = pd.DataFrame([{
        "entry_signal": e, "drop_level": d, "exit_signal": x,
        "buy_pct": b, "alloc_base": ab, "alloc_x2": ax2,
        "alloc_x3": round(1 - ax2, 4),
        "cagr": round(c * 100, 4),
        "worst_ann_ret": round(w * 100, 4),
        "max_dd": round(mdd * 100, 4),
        "passed": ok,
    } for (e, d, x, b, ab, ax2), c, w, mdd, ok in records])
    # Calmar = CAGR / |maxDD|. Guard against div-by-zero (a combo that never
    # drew down — e.g. stayed in cash all-history). |maxDD| floored at 1%.
    out["calmar"] = (out["cagr"] / out["max_dd"].abs().clip(lower=1.0)).round(4)
    return out
