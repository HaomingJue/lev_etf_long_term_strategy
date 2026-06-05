# ==========================================================
# LEVERAGED STRATEGY BACKTESTER v5
#
# Presets: QQQ / SPY / IWM
#
# BUY CYCLE RULES:
#   Arm condition : price closes above MA200 × entry_signal
#   Trigger       : armed AND same-day drop >= drop_level
#
#   First signal in a cycle:
#     1. Buy base up to alloc_base × total_portfolio (one shot)
#     2. Then spend min(buy_pct × total, remaining cash) on lev
#        split by alloc_x2 / alloc_x3
#
#   Subsequent signals (base already filled):
#     - Spend min(buy_pct × total, cash) on lev only
#
# EXIT RULES  (price < exit_MA × exit_signal):
#   exit_MA is MA50, MA100, or MA200 (set via --exit-ma, default 200)
#   Arm/entry always uses MA200.
#   1. Sell ALL 2× and 3× holdings → cash
#   2. If base value > alloc_base × total → trim excess → cash
#   3. Dis-arm: next buy cycle needs a fresh arm
#
# All trades execute at the closing price of the signal day.
# ==========================================================

import argparse
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import yfinance as yf
_no_show = "--no-show" in sys.argv
import matplotlib
if _no_show:
    matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import datetime
import warnings
warnings.filterwarnings("ignore")


# ----------------------------------------------------------
# PRESET CONFIGS
# ----------------------------------------------------------

PRESETS = {
    "QQQ": dict(base="QQQ", lev2="QLD",  lev3="TQQQ"),
    "SPY": dict(base="SPY", lev2="SSO",  lev3="UPRO"),
    "IWM": dict(base="IWM", lev2="UWM",  lev3="TNA"),
}

# Annual management expense ratios for leveraged ETFs.
# Applied ONLY to the synthetic pre-inception period — real ETF prices
# already have MER baked in via daily NAV adjustments.
MER_3X = {"QQQ": 0.0095, "SPY": 0.0091, "IWM": 0.0109}  # TQQQ/UPRO/TNA
MER_2X = {"QQQ": 0.0095, "SPY": 0.0089, "IWM": 0.0095}  # QLD/SSO/UWM

# Earliest date yfinance has reliable data for each base ETF
PRESET_INCEPTION = {
    "QQQ": "1999-01-01",   # QQQ launched 1999-03-10
    "SPY": "1993-01-29",   # SPY launched 1993-01-29
    "IWM": "2000-05-22",   # IWM launched 2000-05-26
}


# ----------------------------------------------------------
# CLI
# ----------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Leveraged ETF strategy backtester (QQQ, SPY, or IWM preset)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--preset",       default="QQQ", choices=["QQQ", "SPY", "IWM"],
                   help="Which base/lev ETF set to use")
    p.add_argument("--start",        default=None,
                   help="Start date (YYYY-MM-DD). Defaults to each preset's inception date.")
    p.add_argument("--end",          default=datetime.date.today().isoformat(),
                   help="End date. Defaults to today")
    p.add_argument("--capital",      type=float, default=10_000)
    p.add_argument("--entry-signal", type=float, default=1.04,
                   help="Arm buys when price > MA200 × entry_signal")
    p.add_argument("--drop-level",   type=float, default=0.01,
                   help="Buy trigger: day drop ≥ drop_level (0.01 = 1%%)")
    p.add_argument("--exit-signal",  type=float, default=1.00,
                   help="Exit when price < MA200 × exit_signal")
    p.add_argument("--buy-pct",      type=float, default=0.20,
                   help="Max lev spend per signal as fraction of total portfolio")
    p.add_argument("--alloc-base",   type=float, default=0.20,
                   help="Target base stock allocation (fraction of portfolio)")
    p.add_argument("--alloc-x2",     type=float, default=0.00,
                   help="Fraction of lev spend going to 2× ETF")
    p.add_argument("--alloc-x3",     type=float, default=1.00,
                   help="Fraction of lev spend going to 3× ETF")
    p.add_argument("--exit-ma",      type=int, default=200, choices=[50, 100, 200],
                   help="MA period used for exit signal (arm/entry always uses MA200)")
    p.add_argument("--save-plot",    default=None,
                   help="Save plot to this path instead of showing interactively")
    p.add_argument("--cost-per-trade", type=float, default=0.0,
                   help="One-way transaction cost as a fraction of trade value "
                        "(e.g. 0.001 = 0.1%%). Applied to every buy and sell execution.")
    p.add_argument("--no-show",     action="store_true",
                   help="Suppress interactive plot window (images still saved if --save-plot is set)")

    args = p.parse_args()

    # Default start = preset inception if not supplied
    if args.start is None:
        args.start = PRESET_INCEPTION[args.preset]

    inception = PRESET_INCEPTION[args.preset]
    if args.start < inception:
        p.error(
            f"--start {args.start} is before {args.preset} inception ({inception}). "
            f"Earliest valid start for {args.preset} is {inception}."
        )

    # alloc_x2 + alloc_x3 must sum to 1 (they split the lev portion)
    lev_alloc = args.alloc_x2 + args.alloc_x3
    if not np.isclose(lev_alloc, 1.0, atol=1e-6):
        p.error(f"--alloc-x2 + --alloc-x3 must equal 1.0 (got {lev_alloc:.4f})")

    # alloc_base must be in [0, 1)
    if not 0.0 <= args.alloc_base < 1.0:
        p.error("--alloc-base must be in [0.0, 1.0)")

    return args


# ----------------------------------------------------------
# DATA
# ----------------------------------------------------------

def download(ticker: str, start: str, end: str) -> pd.Series:
    raw = yf.download(ticker, start=start, end=end,
                      auto_adjust=True, progress=False)
    if raw.empty:
        raise ValueError(f"No data for {ticker}. Check ticker / date range.")
    s = raw["Close"].squeeze()
    s.name = ticker
    return s.dropna()


def align(*series: pd.Series) -> pd.DataFrame:
    """Inner-join all series on their shared trading dates."""
    df = pd.concat(series, axis=1, join="inner")
    return df.dropna()

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

def build_lev_nav(qqq: pd.Series, real: pd.Series, L: int,
                  annual_mer: float = 0.0) -> pd.Series:
    """
    Returns a NAV series (starting at 1.0 on qqq.index[0]) for an
    L-times leveraged ETF, using synthetic returns before real ETF
    inception and real prices (re-scaled) from inception onward.

    annual_mer: expense ratio applied only to the synthetic period
    (e.g. 0.0095 for TQQQ). Real ETF prices already include MER.
    """
    ret   = qqq.pct_change().fillna(0)
    var20 = ret.rolling(20).var().fillna(0)
    daily_mer = annual_mer / 252.0

    # Find stitch point before the loop so we can apply MER selectively
    first_real_pos = None
    if real is not None and not real.dropna().empty:
        common = qqq.index.intersection(real.dropna().index)
        if not common.empty:
            first_real_pos = qqq.index.get_loc(common[0])

    nav = np.empty(len(qqq))
    nav[0] = 1.0
    r_arr = ret.values
    v_arr = var20.values
    for i in range(1, len(qqq)):
        r = r_arr[i]; v = v_arr[i]
        lev_r = L * r - 0.5 * (L**2 - L) * v
        if first_real_pos is None or i < first_real_pos:
            lev_r -= daily_mer   # MER applies only during synthetic period
        nav[i] = nav[i-1] * (1.0 + lev_r)
    synth = pd.Series(nav, index=qqq.index, name=f"synth{L}x")

    if first_real_pos is None:
        return synth

    first_real = qqq.index[first_real_pos]
    scale = synth.loc[first_real] / real.loc[first_real]
    real_scaled = real.reindex(qqq.index) * scale

    stitched = synth.copy()
    stitched.loc[first_real:] = real_scaled.loc[first_real:]
    return stitched



# ----------------------------------------------------------
# INDICATORS
# ----------------------------------------------------------

def add_indicators(df: pd.DataFrame, base_col: str) -> pd.DataFrame:
    df = df.copy()
    df["ret"]   = df[base_col].pct_change().fillna(0)
    df["MA50"]  = df[base_col].rolling(50).mean()
    df["MA100"] = df[base_col].rolling(100).mean()
    df["MA200"] = df[base_col].rolling(200).mean()
    return df


# ----------------------------------------------------------
# NAV NORMALISATION
# Converts each price series to a NAV starting at 1.0
# on the first row, so that  shares × NAV = $ value.
# ----------------------------------------------------------

def normalise(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    for c in cols:
        df[c] = df[c] / df[c].iloc[0]
    return df


# ----------------------------------------------------------
# BACKTEST ENGINE
# ----------------------------------------------------------

def run_backtest(args, param_schedule=None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cfg = PRESETS[args.preset]
    base_tk = cfg["base"]
    lev2_tk = cfg["lev2"]
    lev3_tk = cfg["lev3"]

    # ── Download base from 1999; real lev ETFs from their inception ──
    print(f"  Downloading {base_tk}, {lev2_tk}, {lev3_tk} …")
    # Use 300-day warmup before args.start for accurate MA200,
    # but never go earlier than the base ETF's inception date.
    warmup_start = max(
        (pd.Timestamp(args.start) - pd.DateOffset(days=300)).strftime("%Y-%m-%d"),
        PRESET_INCEPTION[args.preset],
    )
    s_base = download(base_tk, warmup_start, args.end)
    # Download real lev ETFs — they'll only have data from their launch date
    try:
        s_lev2_real = download(lev2_tk, warmup_start, args.end)
    except Exception:
        s_lev2_real = pd.Series(dtype=float)
    try:
        s_lev3_real = download(lev3_tk, warmup_start, args.end)
    except Exception:
        s_lev3_real = pd.Series(dtype=float)

    # Build stitched NAV series (synthetic before inception, real after)
    # MER is applied only to the synthetic portion; real prices already include it.
    lev2_nav = build_lev_nav(s_base, s_lev2_real, 2, annual_mer=MER_2X[args.preset])
    lev3_nav = build_lev_nav(s_base, s_lev3_real, 3, annual_mer=MER_3X[args.preset])

    # Assemble full-history DataFrame on base index
    df_full = pd.DataFrame({
        "base": s_base,
        "lev2": lev2_nav,
        "lev3": lev3_nav,
    }).dropna(subset=["base"])

    # ── Indicators on full warmup history ─────────────────
    df_full = add_indicators(df_full, "base")

    # ── Trim to user-requested start date ─────────────────
    df = df_full[df_full.index >= args.start].copy()

    # s_base for display (un-normalised actual prices)
    s_base = s_base.reindex(df.index)

    # ── NAV series (each starts at 1.0 on first strategy day) ──
    df = normalise(df, ["base", "lev2", "lev3"])

    # Buy-and-hold benchmark: starts at capital on day 0 of strategy
    df["BuyHold"] = args.capital * df["base"]

    # ── State ─────────────────────────────────────────────
    cash  = args.capital
    s_b   = 0.0      # units of base held
    s_2   = 0.0      # units of lev2 held
    s_3   = 0.0      # units of lev3 held

    armed           = False   # True once price re-crosses entry threshold
    base_filled     = False   # True after base is bought for the first time
    base_trimmed    = False   # True after the one-time exit trim has fired

    # Weighted avg base-stock price for lev gain% on exit
    lev_price_wsum  = 0.0
    lev_dollar_sum  = 0.0

    history      = []
    transactions = []

    START_IDX = 1     # MA200 already valid from day 0 (warmup handled above)

    exit_ma_col = f"MA{args.exit_ma}"

    # Active params — fixed unless param_schedule overrides them at year boundaries
    active_entry = args.entry_signal
    active_exit  = args.exit_signal
    active_drop  = args.drop_level
    active_buy   = args.buy_pct
    active_base  = args.alloc_base
    active_x2    = args.alloc_x2
    active_x3    = args.alloc_x3
    _sched_year  = None

    raw_base    = df["base"].values
    raw_lev2    = df["lev2"].values
    raw_lev3    = df["lev3"].values
    raw_ret     = df["ret"].values
    raw_ma200   = df["MA200"].values   # always used for arm/entry
    raw_ma_exit = df[exit_ma_col].values  # used for exit
    idx         = df.index

    # Actual (un-normalised) base price for display
    base_price_series = s_base.reindex(df.index)

    # Record day 0 — strategy starts fully in cash at args.capital
    history.append({
        "Date":     idx[0],
        "Price":    base_price_series.iloc[0],
        "Strategy": args.capital,
        "BuyHold":  df["BuyHold"].iloc[0],
    })

    for i in range(START_IDX, len(df)):

        if param_schedule is not None:
            yr = idx[i].year
            if yr != _sched_year:
                _sched_year = yr
                if yr in param_schedule:
                    p = param_schedule[yr]
                    active_entry = p["entry_signal"]
                    active_exit  = p["exit_signal"]
                    active_drop  = p["drop_level"]
                    active_buy   = p["buy_pct"]
                    active_base  = p.get("alloc_base", args.alloc_base)
                    active_x2    = p.get("alloc_x2",   args.alloc_x2)
                    active_x3    = p.get("alloc_x3",   args.alloc_x3)

        nb      = raw_base[i]
        n2      = raw_lev2[i]
        n3      = raw_lev3[i]
        ma200   = raw_ma200[i]
        ma_exit = raw_ma_exit[i]
        ret     = raw_ret[i]

        if np.isnan(ma200) or ma200 == 0 or np.isnan(ma_exit) or ma_exit == 0:
            continue

        # actual base price (for display & signal calc)
        price = base_price_series.iloc[i]
        prev  = base_price_series.iloc[i - 1]

        val_b    = s_b * nb
        val_2    = s_2 * n2
        val_3    = s_3 * n3
        holdings = val_b + val_2 + val_3
        total    = cash + holdings

        # ══════════════════════════════════════════════════
        # EXIT  (uses exit MA — MA50, MA100, or MA200)
        # ══════════════════════════════════════════════════
        if price < ma_exit * active_exit and (s_2 > 0 or s_3 > 0):

            notes = []

            # 1. Sell all lev
            lev_val = val_2 + val_3
            if lev_val > 0:
                avg_px   = (lev_price_wsum / lev_dollar_sum
                            if lev_dollar_sum > 0 else 0.0)
                gain_pct = ((price / avg_px) - 1) * 100 if avg_px > 0 else 0.0
                cash    += lev_val
                cash    -= lev_val * args.cost_per_trade   # transaction cost on exit
                s_2 = s_3 = 0.0
                lev_price_wsum = lev_dollar_sum = 0.0
                notes.append(f"lev gain {gain_pct:+.2f}%")

            # 2. Trim base if over target — ONE TIME ONLY, never again
            if not base_trimmed and active_base > 0:
                val_b  = s_b * nb
                total  = cash + val_b
                target = total * active_base
                if val_b > target + 0.01:
                    excess        = val_b - target
                    shares_trim   = excess / nb
                    s_b          -= shares_trim
                    cash         += excess
                    total         = cash + s_b * nb
                    notes.append(f"base trimmed ${excess:,.2f}")
                base_trimmed = True   # never trim again regardless

            # Dis-arm: need fresh entry signal for next cycle.
            # base_filled stays True — base is never fully sold,
            # so the next cycle does NOT re-buy base on its first signal.
            armed = False

            transactions.append({
                "Year":       idx[i].year,
                "Date":       str(idx[i].date()),
                "Type":       "EXIT",
                "Base Price": round(price, 2),
                "Portfolio":  round(total, 2),
                "Note":       " | ".join(notes) if notes else "—",
            })

        else:
            # ════════════════════════════════════════════
            # ARM CHECK  (always MA200)
            # ════════════════════════════════════════════
            if not armed and price > ma200 * active_entry:
                armed = True

            # ════════════════════════════════════════════
            # BUY SIGNAL  (always MA200)
            # ════════════════════════════════════════════
            drop = (prev - price) / prev if prev > 0 else 0.0
            is_drop_signal = (
                armed
                and price > ma200 * active_entry
                and drop >= active_drop
                and cash > 0.01
            )

            if is_drop_signal:
                buy_notes = []

                # ── First signal in cycle: fill base ────
                if not base_filled and active_base > 0:
                    target_base_val = total * active_base
                    current_base_val = s_b * nb
                    base_needed = max(target_base_val - current_base_val, 0.0)
                    base_spend  = min(base_needed, cash)

                    if base_spend > 0.01:
                        s_b  += base_spend / nb
                        cash -= base_spend
                        cash -= base_spend * args.cost_per_trade  # transaction cost
                        buy_notes.append(f"base filled ${base_spend:,.2f}")

                    base_filled = True

                    # Recalc total after base buy
                    total = cash + s_b * nb + s_2 * n2 + s_3 * n3

                # ── Lev buy (every signal) ───────────────
                lev_spend = min(active_buy * total, cash)

                if lev_spend > 0.01:
                    a2 = lev_spend * active_x2
                    a3 = lev_spend * active_x3

                    if a2 > 0:
                        s_2            += a2 / n2
                        lev_price_wsum += price * a2
                        lev_dollar_sum += a2
                    if a3 > 0:
                        s_3            += a3 / n3
                        lev_price_wsum += price * a3
                        lev_dollar_sum += a3

                    cash -= lev_spend
                    cash -= lev_spend * args.cost_per_trade  # transaction cost
                    buy_notes.append(f"lev ${lev_spend:,.2f}")

                # Recalc for snapshot
                holdings = s_b * nb + s_2 * n2 + s_3 * n3
                total    = cash + holdings

                transactions.append({
                    "Year":       idx[i].year,
                    "Date":       str(idx[i].date()),
                    "Type":       "BUY",
                    "Base Price": round(price, 2),
                    "Portfolio":  round(total, 2),
                    "Note":       " | ".join(buy_notes),
                })

        # ── Daily snapshot ────────────────────────────────
        holdings = s_b * nb + s_2 * n2 + s_3 * n3
        total    = cash + holdings

        history.append({
            "Date":     idx[i],
            "Price":    price,
            "Strategy": total,
            "BuyHold":  df["BuyHold"].iloc[i],
        })

    hist = pd.DataFrame(history).set_index("Date")

    # ── Yearly summary ────────────────────────────────────
    yearly = hist.resample("YE").last()
    rows   = []
    prev_s = args.capital
    prev_b = args.capital

    for i in range(len(yearly)):
        yr  = yearly.index[i].year
        s_v = yearly["Strategy"].iloc[i]
        b_v = yearly["BuyHold"].iloc[i]
        rows.append({
            "Year":              yr,
            f"{base_tk} Ret %":  round((b_v / prev_b - 1) * 100, 2),
            f"{base_tk} Value":  round(b_v, 2),
            "Strategy Ret %":    round((s_v / prev_s - 1) * 100, 2),
            "Strategy Value":    round(s_v, 2),
        })
        prev_s = s_v
        prev_b = b_v

    year_df  = pd.DataFrame(rows)
    trans_df = pd.DataFrame(transactions)

    return hist, year_df, trans_df, base_tk


# ----------------------------------------------------------
# CAGR
# ----------------------------------------------------------

def cagr(end_val: float, start_val: float, days: int) -> float:
    return (end_val / start_val) ** (365.25 / days) - 1


# ----------------------------------------------------------
# AUTO-SAVE HELPERS
# ----------------------------------------------------------

def _run_slug(args) -> str:
    """Descriptive filename stem from run parameters."""
    start_yr = args.start[:4]
    end_yr   = args.end[:4]
    b  = int(round(args.alloc_base * 100))
    x2 = int(round(args.alloc_x2   * 100))
    return (
        f"{args.preset}_{start_yr}-{end_yr}"
        f"_entry{args.entry_signal}"
        f"_exit{args.exit_signal}"
        f"_drop{args.drop_level}"
        f"_buy{args.buy_pct}"
        f"_b{b}_x2{x2}"
        f"_ma{args.exit_ma}"
    )


def _auto_out_dir(args) -> Path:
    out = Path(__file__).parent / "results" / "backtester" / args.preset
    out.mkdir(parents=True, exist_ok=True)
    return out


def _compute_metrics(hist, year_df, capital):
    """Return (bcagr, scagr, worst_yr, max_dd_pct, sharpe) for a hist DataFrame."""
    days     = (hist.index[-1] - hist.index[0]).days
    bcagr    = cagr(hist["BuyHold"].iloc[-1],  capital, days)
    scagr    = cagr(hist["Strategy"].iloc[-1], capital, days)
    worst_yr = year_df["Strategy Ret %"].min()

    # Intra-period peak-to-trough drawdown
    roll_max = hist["Strategy"].cummax()
    max_dd   = ((hist["Strategy"] - roll_max) / roll_max).min() * 100

    # Annualised Sharpe (rf = 0%)
    daily_ret = hist["Strategy"].pct_change().dropna()
    sharpe    = (daily_ret.mean() / daily_ret.std() * np.sqrt(252)
                 if daily_ret.std() > 0 else 0.0)

    return bcagr, scagr, worst_yr, max_dd, sharpe


def save_results_files(hist, year_df, trans_df, base_tk, args):
    """Save yearly CSV + summary TXT when running with --no-show."""
    out_dir  = _auto_out_dir(args)
    slug     = _run_slug(args)
    bcagr, scagr, worst_yr, max_dd, sharpe = _compute_metrics(
        hist, year_df, args.capital)

    year_df.to_csv(out_dir / f"{slug}_yearly.csv", index=False)

    summary_lines = [
        f"Preset          : {args.preset}",
        f"Period          : {args.start} -> {args.end}",
        f"Entry signal    : {args.entry_signal}x MA200",
        f"Exit signal     : {args.exit_signal}x MA{args.exit_ma}",
        f"Drop level      : {args.drop_level*100:.2f}%",
        f"Buy pct         : {args.buy_pct*100:.0f}% per signal",
        f"Alloc base      : {args.alloc_base*100:.0f}%  lev2 {args.alloc_x2*100:.0f}%  lev3 {args.alloc_x3*100:.0f}%",
        f"Cost per trade  : {args.cost_per_trade*100:.3f}%",
        f"",
        f"Strategy CAGR   : {scagr*100:.2f}%",
        f"B&H CAGR ({base_tk:3s})  : {bcagr*100:.2f}%",
        f"Strategy edge   : {(scagr-bcagr)*100:+.2f}pp",
        f"Final value     : ${hist['Strategy'].iloc[-1]:,.2f}",
        f"Worst year      : {worst_yr:.2f}%",
        f"Max drawdown    : {max_dd:.2f}%",
        f"Sharpe ratio    : {sharpe:.2f}",
        f"Total trades    : {len(trans_df)}",
    ]
    (out_dir / f"{slug}_summary.txt").write_text("\n".join(summary_lines))
    print(f"  Saved: results/backtester/{args.preset}/{slug}_yearly.csv")
    print(f"  Saved: results/backtester/{args.preset}/{slug}_summary.txt")


# ----------------------------------------------------------
# OUTPUT
# ----------------------------------------------------------

def print_results(hist, year_df, trans_df, base_tk, args):
    W = 75

    print("\n" + "=" * W)
    print("  TRANSACTIONS")
    print("=" * W)
    if trans_df.empty:
        print("  [No transactions triggered.]")
    else:
        print(trans_df.to_string(index=False))

    print("\n" + "=" * W)
    print("  YEARLY RETURNS")
    print("=" * W)
    print(year_df.to_string(index=False))

    bcagr, scagr, worst_yr, max_dd, sharpe = _compute_metrics(
        hist, year_df, args.capital)

    print("\n" + "=" * W)
    print("  SUMMARY")
    print("=" * W)
    print(f"  {base_tk} Buy & Hold CAGR : {bcagr * 100:7.2f}%   "
          f"Final: ${hist['BuyHold'].iloc[-1]:>12,.2f}")
    print(f"  Strategy CAGR         : {scagr * 100:7.2f}%   "
          f"Final: ${hist['Strategy'].iloc[-1]:>12,.2f}")
    print(f"  Strategy edge         : {(scagr-bcagr)*100:+7.2f}pp")
    print(f"  Worst year            : {worst_yr:7.2f}%")
    print(f"  Max drawdown          : {max_dd:7.2f}%")
    green_yrs   = (year_df["Strategy Ret %"] > 0).sum()
    total_yrs   = len(year_df)
    outperf_yrs = (year_df["Strategy Ret %"] > year_df[f"{base_tk} Ret %"]).sum()
    print(f"  Sharpe ratio          : {sharpe:7.2f}")
    print(f"  Green years           : {green_yrs}/{total_yrs}  ({green_yrs/total_yrs*100:.0f}%)")
    print(f"  Beat {base_tk:<3} years       : {outperf_yrs}/{total_yrs}  ({outperf_yrs/total_yrs*100:.0f}%)")
    if args.cost_per_trade > 0:
        print(f"  Cost per trade        : {args.cost_per_trade*100:.3f}%")
    print()


def plot_results(hist, base_tk, args):
    cfg = PRESETS[args.preset]
    fig, ax = plt.subplots(figsize=(14, 7))

    ax.plot(hist.index, hist["BuyHold"],  label=f"{base_tk} Buy & Hold",
            linewidth=1.5, color="steelblue")
    ax.plot(hist.index, hist["Strategy"], label="Leveraged Strategy",
            linewidth=1.5, color="darkorange")

    lev_label = (f"2x={cfg['lev2']} {args.alloc_x2*100:.0f}% / "
                 f"3x={cfg['lev3']} {args.alloc_x3*100:.0f}%")
    ax.set_title(
        f"Backtest — {base_tk}  |  "
        f"Entry >{args.entry_signal}x MA200 & drop >{args.drop_level*100:.1f}%  |  "
        f"Exit <{args.exit_signal}x MA{args.exit_ma}\n"
        f"Base {args.alloc_base*100:.0f}%  |  Lev: {lev_label}  |  "
        f"Lev buy size: {args.buy_pct*100:.0f}% per signal",
        fontsize=10,
    )
    ax.set_xlabel("Date")
    ax.set_ylabel("Portfolio Value ($)")
    ax.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.4)
    plt.tight_layout()
    if args.save_plot:
        path = args.save_plot
    elif args.no_show:
        out_dir = _auto_out_dir(args)
        path = out_dir / f"{_run_slug(args)}.png"
    else:
        path = None

    if path:
        plt.savefig(path, dpi=150)
        print(f"  Saved: {path}")
        plt.close()
    else:
        plt.show(block=True)

    # --- drawdown figure ---
    bh_dd    = (hist["BuyHold"]  / hist["BuyHold"].cummax()  - 1) * 100
    strat_dd = (hist["Strategy"] / hist["Strategy"].cummax() - 1) * 100

    fig2, ax2 = plt.subplots(figsize=(14, 5))
    ax2.plot(hist.index, bh_dd,    color="steelblue",  linewidth=1.2,
             label=f"{base_tk} B&H")
    ax2.plot(hist.index, strat_dd, color="darkorange", linewidth=1.2,
             label="Strategy")
    ax2.fill_between(hist.index, strat_dd, 0, alpha=0.15, color="darkorange")
    ax2.set_title(
        f"Drawdown — {base_tk}  |  "
        f"Entry >{args.entry_signal}x MA200 & drop >{args.drop_level*100:.1f}%  |  "
        f"Exit <{args.exit_signal}x MA{args.exit_ma}",
        fontsize=10,
    )
    ax2.set_ylabel("Drawdown (%)")
    ax2.set_xlabel("Date")
    ax2.legend(fontsize=11)
    ax2.grid(True, alpha=0.4)
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    plt.tight_layout()

    if args.save_plot:
        p = Path(args.save_plot)
        dd_path = p.with_name(p.stem + "_drawdown" + p.suffix)
    elif args.no_show:
        out_dir = _auto_out_dir(args)
        dd_path = out_dir / f"{_run_slug(args)}_drawdown.png"
    else:
        dd_path = None

    if dd_path:
        plt.savefig(dd_path, dpi=150)
        print(f"  Saved: {dd_path}")
        plt.close()
    else:
        plt.show(block=True)


# ----------------------------------------------------------
# ENTRY POINT
# ----------------------------------------------------------

if __name__ == "__main__":
    args = parse_args()
    cfg  = PRESETS[args.preset]

    print(f"\n{'='*60}")
    print(f"  Preset   : {args.preset}  "
          f"({cfg['base']} / {cfg['lev2']} / {cfg['lev3']})")
    print(f"  Period   : {args.start} -> {args.end}")
    print(f"  Capital  : ${args.capital:,.0f}")
    print(f"  Arm      : price > {args.entry_signal}x MA200")
    print(f"  Buy      : armed + drop >= {args.drop_level*100:.1f}%")
    print(f"  Exit     : price < {args.exit_signal}x MA{args.exit_ma}")
    print(f"  Alloc    : base {args.alloc_base*100:.0f}%  |  "
          f"2x {args.alloc_x2*100:.0f}%  |  3x {args.alloc_x3*100:.0f}%")
    print(f"  Lev buy  : min({args.buy_pct*100:.0f}% of portfolio, cash) per signal")
    print(f"{'='*60}")

    hist, year_df, trans_df, base_tk = run_backtest(args)
    print_results(hist, year_df, trans_df, base_tk, args)
    plot_results(hist, base_tk, args)
    if args.no_show:
        save_results_files(hist, year_df, trans_df, base_tk, args)