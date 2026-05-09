# ==========================================================
# LEVERAGED STRATEGY BACKTESTER v4
#
# Two hardcoded configs:
#   QQQ  →  QLD (2×)  +  TQQQ (3×)
#   SPY  →  SSO (2×)  +  UPRO (3×)
#
# BUY CYCLE RULES:
#   Arm condition : price closes above MA200 × entry_signal
#   Trigger       : armed AND same-day drop ≥ drop_level
#
#   First signal in a cycle:
#     1. Buy base up to alloc_base × total_portfolio (one shot)
#     2. Then spend min(buy_pct × total, remaining cash) on lev
#        split by alloc_x2 / alloc_x3
#
#   Subsequent signals (base already filled):
#     - Spend min(buy_pct × total, cash) on lev only
#
# EXIT RULES  (price < MA200 × exit_signal):
#   1. Sell ALL 2× and 3× holdings → cash
#   2. If base value > alloc_base × total → trim excess → cash
#   3. Dis-arm: next buy cycle needs a fresh arm
#
# All trades execute at the closing price of the signal day.
# ==========================================================

import argparse
import sys
import pandas as pd
import numpy as np
import yfinance as yf
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
    p.add_argument("--start",        default="1999-03-10",
                   help="Start date. Defaults to QQQ inception (1999-03-10)")
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
    p.add_argument("--save-plot",    default=None,
                   help="Save plot to this path instead of showing interactively")

    args = p.parse_args()

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
# INDICATORS
# ----------------------------------------------------------

def add_indicators(df: pd.DataFrame, base_col: str) -> pd.DataFrame:
    df = df.copy()
    df["ret"]   = df[base_col].pct_change().fillna(0)
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

def run_backtest(args) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cfg = PRESETS[args.preset]
    base_tk = cfg["base"]
    lev2_tk = cfg["lev2"]
    lev3_tk = cfg["lev3"]

    # ── Download base from 1999; real lev ETFs from their inception ──
    print(f"  Downloading {base_tk}, {lev2_tk}, {lev3_tk} …")
    # Use 300-day warmup before args.start for accurate MA200,
    # but never go earlier than 1999-01-01 (QQQ inception)
    warmup_start = max(
        (pd.Timestamp(args.start) - pd.DateOffset(days=300)).strftime("%Y-%m-%d"),
        "1999-01-01"
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
    lev2_nav = build_lev_nav(s_base, s_lev2_real, 2)
    lev3_nav = build_lev_nav(s_base, s_lev3_real, 3)

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

    raw_base = df["base"].values
    raw_lev2 = df["lev2"].values
    raw_lev3 = df["lev3"].values
    raw_ret  = df["ret"].values
    raw_ma   = df["MA200"].values
    idx      = df.index

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

        nb   = raw_base[i]          # base NAV
        n2   = raw_lev2[i]          # lev2 NAV
        n3   = raw_lev3[i]          # lev3 NAV
        ma   = raw_ma[i]
        ret  = raw_ret[i]

        if np.isnan(ma) or ma == 0:
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
        # EXIT
        # ══════════════════════════════════════════════════
        if price < ma * args.exit_signal and (s_2 > 0 or s_3 > 0):

            notes = []

            # 1. Sell all lev
            lev_val = val_2 + val_3
            if lev_val > 0:
                avg_px   = (lev_price_wsum / lev_dollar_sum
                            if lev_dollar_sum > 0 else 0.0)
                gain_pct = ((price / avg_px) - 1) * 100 if avg_px > 0 else 0.0
                cash    += lev_val
                s_2 = s_3 = 0.0
                lev_price_wsum = lev_dollar_sum = 0.0
                notes.append(f"lev gain {gain_pct:+.2f}%")

            # 2. Trim base if over target — ONE TIME ONLY, never again
            if not base_trimmed and args.alloc_base > 0:
                val_b  = s_b * nb
                total  = cash + val_b
                target = total * args.alloc_base
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
            # ARM CHECK
            # ════════════════════════════════════════════
            if not armed and price > ma * args.entry_signal:
                armed = True

            # ════════════════════════════════════════════
            # BUY SIGNAL
            # ════════════════════════════════════════════
            drop = (prev - price) / prev if prev > 0 else 0.0
            is_drop_signal = (
                armed
                and price > ma * args.entry_signal
                and drop >= args.drop_level
                and cash > 0.01
            )

            if is_drop_signal:
                buy_notes = []

                # ── First signal in cycle: fill base ────
                if not base_filled and args.alloc_base > 0:
                    target_base_val = total * args.alloc_base
                    current_base_val = s_b * nb
                    base_needed = max(target_base_val - current_base_val, 0.0)
                    base_spend  = min(base_needed, cash)

                    if base_spend > 0.01:
                        s_b  += base_spend / nb
                        cash -= base_spend
                        buy_notes.append(f"base filled ${base_spend:,.2f}")

                    base_filled = True

                    # Recalc total after base buy
                    total = cash + s_b * nb + s_2 * n2 + s_3 * n3

                # ── Lev buy (every signal) ───────────────
                lev_spend = min(args.buy_pct * total, cash)

                if lev_spend > 0.01:
                    a2 = lev_spend * args.alloc_x2
                    a3 = lev_spend * args.alloc_x3

                    if a2 > 0:
                        s_2            += a2 / n2
                        lev_price_wsum += price * a2
                        lev_dollar_sum += a2
                    if a3 > 0:
                        s_3            += a3 / n3
                        lev_price_wsum += price * a3
                        lev_dollar_sum += a3

                    cash -= lev_spend
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

    days  = (hist.index[-1] - hist.index[0]).days
    bcagr = cagr(hist["BuyHold"].iloc[-1],  args.capital, days)
    scagr = cagr(hist["Strategy"].iloc[-1], args.capital, days)

    print("\n" + "=" * W)
    print("  CAGR & FINAL VALUES")
    print("=" * W)
    print(f"  {base_tk} Buy & Hold CAGR : {bcagr * 100:7.2f}%   "
          f"Final: ${hist['BuyHold'].iloc[-1]:>12,.2f}")
    print(f"  Strategy CAGR         : {scagr * 100:7.2f}%   "
          f"Final: ${hist['Strategy'].iloc[-1]:>12,.2f}")
    print()


def plot_results(hist, base_tk, args):
    cfg = PRESETS[args.preset]
    fig, ax = plt.subplots(figsize=(14, 7))

    ax.plot(hist.index, hist["BuyHold"],  label=f"{base_tk} Buy & Hold",
            linewidth=1.5, color="steelblue")
    ax.plot(hist.index, hist["Strategy"], label="Leveraged Strategy",
            linewidth=1.5, color="darkorange")

    lev_label = (f"2×={cfg['lev2']} {args.alloc_x2*100:.0f}% / "
                 f"3×={cfg['lev3']} {args.alloc_x3*100:.0f}%")
    ax.set_title(
        f"Backtest — {base_tk}  |  "
        f"Entry >{args.entry_signal}×MA200 & drop >{args.drop_level*100:.1f}%  |  "
        f"Exit <{args.exit_signal}×MA200\n"
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
        plt.savefig(args.save_plot, dpi=150)
        print(f"  Saved: {args.save_plot}")
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
    print(f"  Exit     : price < {args.exit_signal}x MA200")
    print(f"  Alloc    : base {args.alloc_base*100:.0f}%  |  "
          f"2x {args.alloc_x2*100:.0f}%  |  3x {args.alloc_x3*100:.0f}%")
    print(f"  Lev buy  : min({args.buy_pct*100:.0f}% of portfolio, cash) per signal")
    print(f"{'='*60}")

    hist, year_df, trans_df, base_tk = run_backtest(args)
    print_results(hist, year_df, trans_df, base_tk, args)
    plot_results(hist, base_tk, args)