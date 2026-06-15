# ==========================================================
# LEVERAGED STRATEGY BACKTESTER v5
#
# Presets: QQQ / SPY / IWM
#
# BUY CYCLE RULES:
#   Arm condition : price closes above MA200 × entry_signal
#   Trigger       : armed AND same-day drop >= drop_level
#
#   Every buy signal:
#     1. Top the base sleeve up to alloc_base × total_portfolio if it is
#        currently below target (fills the gap each time, not just once).
#     2. Then spend min(buy_pct × total, (1 - alloc_base) × total, cash) on
#        lev, split by alloc_x2 / alloc_x3. The single-buy lev size can never
#        exceed (1 - alloc_base) of the portfolio — the base weight is always
#        reserved (e.g. base 20% ⇒ a lev buy is capped at 80% of total).
#
# EXIT RULES  (price < exit_MA × exit_signal):
#   exit_MA is MA50, MA100, or MA200 (set via --exit-ma, default 200)
#   Arm/entry always uses MA200.
#   1. Sell ALL 2× and 3× holdings → cash
#   2. If base value > alloc_base × total → trim excess → cash (every exit)
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
# ONTARIO TAX MODEL (--tax-ontario)
#
# Combined federal + Ontario personal income tax for a taxable
# (non-registered) account. 2025 brackets, held constant across
# the whole backtest (simplification — brackets are inflation-
# indexed annually).
#
# - Capital gains: 50% inclusion, taxed at the marginal rate
#   stacked on top of --salary. Canada has no short/long-term
#   distinction. Net capital losses carry forward.
# - T-bill interest (--cash-yield): 100% taxable as income.
# - Tax on year Y's realized gains is paid from cash on the
#   first trading day of year Y+1 (sells holdings if cash is
#   short, which itself realizes gains).
# - Ignores CPP/EI, Ontario Health Premium, and credits beyond
#   the basic personal amounts. Assumes capital-gains treatment
#   (not business income — fine at this strategy's 2-4 round
#   trips/yr).
# - In a TFSA or RRSP none of this applies — do NOT use this
#   flag when modeling a registered account.
# ----------------------------------------------------------

FED_BRACKETS = [(57_375, 0.15), (114_750, 0.205), (177_882, 0.26),
                (253_414, 0.29), (float("inf"), 0.33)]
ON_BRACKETS  = [(52_886, 0.0505), (105_775, 0.0915), (150_000, 0.1116),
                (220_000, 0.1216), (float("inf"), 0.1316)]
FED_BPA = 16_129    # federal basic personal amount (credit @ 15%)
ON_BPA  = 12_747    # Ontario basic personal amount (credit @ 5.05%)
ON_SURTAX_T1, ON_SURTAX_T2 = 5_710, 7_307   # Ontario surtax thresholds
CG_INCLUSION = 0.50


def _bracket_tax(income: float, brackets) -> float:
    tax, lower = 0.0, 0.0
    for upper, rate in brackets:
        if income <= lower:
            break
        tax += (min(income, upper) - lower) * rate
        lower = upper
    return tax


def _ontario_total_tax(taxable: float) -> float:
    if taxable <= 0:
        return 0.0
    fed = max(_bracket_tax(taxable, FED_BRACKETS)
              - 0.15 * min(FED_BPA, taxable), 0.0)
    on  = max(_bracket_tax(taxable, ON_BRACKETS)
              - 0.0505 * min(ON_BPA, taxable), 0.0)
    surtax = (0.20 * max(on - ON_SURTAX_T1, 0.0)
              + 0.36 * max(on - ON_SURTAX_T2, 0.0))
    return fed + on + surtax


def _ontario_tax_on_investment(salary: float, taxable_gains: float,
                               interest: float) -> float:
    """Incremental tax from investment income stacked on top of salary."""
    extra = max(taxable_gains, 0.0) + max(interest, 0.0)
    if extra <= 0:
        return 0.0
    return _ontario_total_tax(salary + extra) - _ontario_total_tax(salary)


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
    p.add_argument("--cash-yield",  action="store_true",
                   help="Accrue daily T-bill interest (^IRX, 13-week rate) on idle "
                        "cash. Models parking uninvested cash in SGOV/BIL/money "
                        "market instead of earning 0%%.")
    p.add_argument("--tax-ontario", action="store_true",
                   help="Model Ontario (Canada) personal income tax in a taxable "
                        "account: capital gains at 50%% inclusion stacked on top "
                        "of --salary, interest 100%% taxable, losses carried "
                        "forward, tax paid each January. Do not use for "
                        "TFSA/RRSP accounts (those are untaxed).")
    p.add_argument("--salary",      type=float, default=100_000,
                   help="Employment income the strategy's gains stack on top of "
                        "(sets the marginal tax rate). Used only with --tax-ontario.")
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

def run_backtest(args, param_schedule=None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str]:
    cfg = PRESETS[args.preset]
    base_tk = cfg["base"]
    lev2_tk = cfg["lev2"]
    lev3_tk = cfg["lev3"]

    # ── Download base from 1999; real lev ETFs from their inception ──
    print(f"  Downloading {base_tk}, {lev2_tk}, {lev3_tk} …")
    # Use 420-day warmup before args.start for accurate MA200
    # (300 calendar days is only ~205 trading days — too tight),
    # but never go earlier than the base ETF's inception date.
    warmup_start = max(
        (pd.Timestamp(args.start) - pd.DateOffset(days=420)).strftime("%Y-%m-%d"),
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

    # Weighted avg base-stock price for lev gain% on exit
    lev_price_wsum  = 0.0
    lev_dollar_sum  = 0.0

    # ── Ontario tax state (--tax-ontario) ─────────────────
    tax_on   = bool(getattr(args, "tax_ontario", False))
    salary   = float(getattr(args, "salary", 100_000.0))
    acb_b = acb_2 = acb_3 = 0.0   # adjusted cost base (CAD average-cost rule)
    realized_y  = 0.0   # net capital gains realized this calendar year
    interest_y  = 0.0   # taxable interest earned this calendar year
    loss_cf     = 0.0   # net capital loss carryforward
    total_tax   = 0.0

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

    # Daily T-bill rate for idle-cash interest (zeros when --cash-yield is off)
    if getattr(args, "cash_yield", False):
        rf_arr = _tbill_daily(df.index).values
    else:
        rf_arr = None

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

        # Idle cash earns the T-bill rate (if --cash-yield)
        if rf_arr is not None and cash > 0:
            interest    = cash * rf_arr[i]
            cash       += interest
            interest_y += interest

        # ── Year boundary: settle last year's Ontario tax ──
        if tax_on and idx[i].year != idx[i - 1].year:
            net = realized_y
            if net < 0:
                loss_cf     += -net
                taxable_gain = 0.0
            else:
                offset       = min(loss_cf, net)
                loss_cf     -= offset
                taxable_gain = (net - offset) * CG_INCLUSION
            tax_due = _ontario_tax_on_investment(salary, taxable_gain, interest_y)
            realized_y = interest_y = 0.0
            if tax_due > 0.01:
                total_tax += tax_due
                if cash >= tax_due:
                    cash -= tax_due
                else:
                    # Sell holdings (3x -> 2x -> base) to cover the shortfall.
                    # These sales realize gains that count toward the new year.
                    shortfall = tax_due - cash
                    cash      = 0.0
                    if shortfall > 0 and s_3 > 0:
                        val  = s_3 * n3
                        sell = min(val, shortfall)
                        frac = sell / val
                        realized_y += sell - acb_3 * frac
                        acb_3 *= 1 - frac
                        s_3   *= 1 - frac
                        shortfall -= sell
                    if shortfall > 0 and s_2 > 0:
                        val  = s_2 * n2
                        sell = min(val, shortfall)
                        frac = sell / val
                        realized_y += sell - acb_2 * frac
                        acb_2 *= 1 - frac
                        s_2   *= 1 - frac
                        shortfall -= sell
                    if shortfall > 0 and s_b > 0:
                        val  = s_b * nb
                        sell = min(val, shortfall)
                        frac = sell / val
                        realized_y += sell - acb_b * frac
                        acb_b *= 1 - frac
                        s_b   *= 1 - frac
                        shortfall -= sell
                transactions.append({
                    "Year":       idx[i].year,
                    "Date":       str(idx[i].date()),
                    "Type":       "TAX",
                    "Base Price": round(base_price_series.iloc[i], 2),
                    "Portfolio":  round(cash + s_b * nb + s_2 * n2 + s_3 * n3, 2),
                    "Note":       f"Ontario tax ${tax_due:,.2f} on prior-year gains",
                })

        if np.isnan(ma200) or ma200 == 0 or np.isnan(ma_exit) or ma_exit == 0:
            # No tradable signal yet — still record the daily snapshot
            history.append({
                "Date":     idx[i],
                "Price":    base_price_series.iloc[i],
                "Strategy": cash + s_b * nb + s_2 * n2 + s_3 * n3,
                "BuyHold":  df["BuyHold"].iloc[i],
            })
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
                realized_y += lev_val - acb_2 - acb_3      # capital gain/loss (ACB)
                acb_2 = acb_3 = 0.0
                s_2 = s_3 = 0.0
                lev_price_wsum = lev_dollar_sum = 0.0
                notes.append(f"lev gain {gain_pct:+.2f}%")

            # 2. Trim base back down to target if it overshot (every exit)
            if active_base > 0:
                val_b  = s_b * nb
                total  = cash + val_b
                target = total * active_base
                if val_b > target + 0.01:
                    excess        = val_b - target
                    shares_trim   = excess / nb
                    frac          = excess / val_b
                    realized_y   += excess - acb_b * frac   # capital gain/loss (ACB)
                    acb_b        *= 1 - frac
                    s_b          -= shares_trim
                    cash         += excess
                    cash         -= excess * args.cost_per_trade  # transaction cost
                    total         = cash + s_b * nb
                    notes.append(f"base trimmed ${excess:,.2f}")

            # Dis-arm: need fresh entry signal for next cycle. The base sleeve
            # is kept (never fully sold) and re-topped on the next buy signal.
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

                # ── Top base up to target on every signal ────
                if active_base > 0:
                    target_base_val = total * active_base
                    current_base_val = s_b * nb
                    base_needed = max(target_base_val - current_base_val, 0.0)
                    base_spend  = min(base_needed, cash)

                    if base_spend > 0.01:
                        s_b   += base_spend / nb
                        acb_b += base_spend
                        cash  -= base_spend
                        cash  -= base_spend * args.cost_per_trade  # transaction cost
                        buy_notes.append(f"base filled ${base_spend:,.2f}")

                    # Recalc total after base buy
                    total = cash + s_b * nb + s_2 * n2 + s_3 * n3

                # ── Lev buy: never exceed (1 - base) of total per signal ──
                eff_buy   = min(active_buy, max(1.0 - active_base, 0.0))
                lev_spend = min(eff_buy * total, cash)

                if lev_spend > 0.01:
                    a2 = lev_spend * active_x2
                    a3 = lev_spend * active_x3

                    if a2 > 0:
                        s_2            += a2 / n2
                        acb_2          += a2
                        lev_price_wsum += price * a2
                        lev_dollar_sum += a2
                    if a3 > 0:
                        s_3            += a3 / n3
                        acb_3          += a3
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

    # Settle the final (possibly partial) year's tax against the last day,
    # so the after-tax final value is honest. Unrealized gains stay deferred.
    if tax_on:
        net    = realized_y
        offset = min(loss_cf, max(net, 0.0))
        taxable_gain = max(net - offset, 0.0) * CG_INCLUSION
        final_tax = _ontario_tax_on_investment(salary, taxable_gain, interest_y)
        if final_tax > 0.01:
            total_tax += final_tax
            history[-1]["Strategy"] -= final_tax

    hist = pd.DataFrame(history).set_index("Date")
    hist.attrs["total_tax"] = total_tax
    hist.attrs["tax_on"]    = tax_on
    hist.attrs["salary"]    = salary

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
    cy = "_cy" if getattr(args, "cash_yield", False) else ""
    if getattr(args, "tax_ontario", False):
        cy += f"_taxON{int(getattr(args, 'salary', 100_000) / 1000)}k"
    return (
        f"{args.preset}_{start_yr}-{end_yr}"
        f"_entry{args.entry_signal}"
        f"_exit{args.exit_signal}"
        f"_drop{args.drop_level}"
        f"_buy{args.buy_pct}"
        f"_b{b}_x2{x2}"
        f"_ma{args.exit_ma}{cy}"
    )


def _auto_out_dir(args) -> Path:
    out = Path(__file__).parent / "results" / "backtester" / args.preset
    out.mkdir(parents=True, exist_ok=True)
    return out


_tbill_cache: pd.Series | None = None

def _tbill_daily(index: pd.DatetimeIndex) -> pd.Series:
    """Return daily risk-free rate aligned to index, sourced from ^IRX (13-week T-bill)."""
    global _tbill_cache
    covered = (_tbill_cache is not None and not _tbill_cache.empty
               and _tbill_cache.index[0] <= index[0]
               # ^IRX quotes can lag a few days — tolerate a short tail gap
               and _tbill_cache.index[-1] >= index[-1] - pd.Timedelta(days=7))
    if not covered:
        start = index[0].strftime("%Y-%m-%d")
        end   = (index[-1] + pd.Timedelta(days=5)).strftime("%Y-%m-%d")
        try:
            _tbill_cache = yf.download("^IRX", start=start, end=end,
                                       auto_adjust=True, progress=False
                                       )["Close"].squeeze().dropna()
        except Exception:
            _tbill_cache = None
    if _tbill_cache is None or _tbill_cache.empty:
        return pd.Series(0.0, index=index)
    # ^IRX is annualised % (e.g. 5.0 = 5%). Convert to daily rate.
    daily = (_tbill_cache / 100) / 252
    return daily.reindex(index, method="ffill").fillna(0.0)


def _compute_metrics(hist, year_df, capital):
    """Return (bcagr, scagr, worst_yr, max_dd_pct, sharpe) for a hist DataFrame."""
    days     = (hist.index[-1] - hist.index[0]).days
    bcagr    = cagr(hist["BuyHold"].iloc[-1],  capital, days)
    scagr    = cagr(hist["Strategy"].iloc[-1], capital, days)
    worst_yr = year_df["Strategy Ret %"].min()

    # Intra-period peak-to-trough drawdown
    roll_max = hist["Strategy"].cummax()
    max_dd   = ((hist["Strategy"] - roll_max) / roll_max).min() * 100

    # Annualised Sharpe using historical T-bill rate (^IRX) as risk-free rate
    daily_ret = hist["Strategy"].pct_change().dropna()
    rf        = _tbill_daily(daily_ret.index)
    excess    = daily_ret - rf
    sharpe    = (excess.mean() / excess.std() * np.sqrt(252)
                 if excess.std() > 0 else 0.0)

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
        f"Cash yield      : {'ON (^IRX T-bill rate on idle cash)' if getattr(args, 'cash_yield', False) else 'off'}",
        f"Ontario tax     : "
        + (f"ON (salary ${args.salary:,.0f}, total tax ${hist.attrs['total_tax']:,.2f})"
           if hist.attrs.get("tax_on") else "off"),
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
    if getattr(args, "cash_yield", False):
        print(f"  Cash yield            : ON (^IRX T-bill rate on idle cash)")
    if hist.attrs.get("tax_on"):
        print(f"  Ontario tax paid      : ${hist.attrs['total_tax']:,.2f}  "
              f"(salary ${hist.attrs['salary']:,.0f}; final value above is after-tax; "
              f"unrealized gains still deferred)")
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

    # Strategy stats box (same basis as the text summary: full-period CAGR,
    # peak-to-trough maxDD, and ^IRX-risk-free annualised Sharpe).
    days      = (hist.index[-1] - hist.index[0]).days
    scagr     = cagr(hist["Strategy"].iloc[-1], args.capital, days) * 100
    strat_max = ((hist["Strategy"] / hist["Strategy"].cummax() - 1).min()) * 100
    daily_ret = hist["Strategy"].pct_change().dropna()
    excess    = daily_ret - _tbill_daily(daily_ret.index)
    sharpe    = (excess.mean() / excess.std() * np.sqrt(252)
                 if excess.std() > 0 else 0.0)
    ax.text(0.015, 0.985,
            f"Strategy\nCAGR  {scagr:.1f}%\nMax DD  {strat_max:.1f}%\n"
            f"Sharpe  {sharpe:.2f}\nFinal  ${hist['Strategy'].iloc[-1]:,.0f}",
            transform=ax.transAxes, fontsize=10, va="top", ha="left",
            family="monospace",
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.8,
                      edgecolor="darkorange"))
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
    print(f"  Lev buy  : min({args.buy_pct*100:.0f}%, "
          f"{max(1-args.alloc_base,0)*100:.0f}% (1-base) of portfolio, cash) per signal")
    if args.cash_yield:
        print(f"  Cash     : earns ^IRX T-bill rate while idle")
    print(f"{'='*60}")

    hist, year_df, trans_df, base_tk = run_backtest(args)
    print_results(hist, year_df, trans_df, base_tk, args)
    plot_results(hist, base_tk, args)
    if args.no_show:
        save_results_files(hist, year_df, trans_df, base_tk, args)