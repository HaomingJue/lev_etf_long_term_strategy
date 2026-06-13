# Leveraged ETFs for the Long Run — A Trend-and-Dip Strategy, Honestly Walk-Forward Tested

A systematic study of whether a disciplined "buy dips only in confirmed uptrends, exit on trend break" rule applied to leveraged ETFs (TQQQ/UPRO, QLD/SSO) can beat buy-and-hold out-of-sample — across the NASDAQ-100, S&P 500, and Russell 2000, with a 72,000-combination grid search, three selection rules, and full expanding-window walk-forward validation.

> [!IMPORTANT]
> **The honest, out-of-sample headline (annual re-optimization, 2015–2026, no look-ahead):**
> - **QQQ works.** Highest-CAGR variant: **33.7% CAGR vs 19.4% buy-and-hold** (+14.3pp), worst year −22.6%. Balanced variant: **24.9% CAGR**, worst year −18.2%, with roughly half the drawdown.
> - **SPY is weak.** Annual re-optimization *underperforms* buy-and-hold out-of-sample (12.3% vs 13.7%). Only frozen 2015 parameters beat it (16.1%). Treat SPY as marginal.
> - **IWM fails** out-of-sample (6.2% vs 9.6% B&H) and is not recommended.
>
> Full-history (in-sample, hindsight-optimized) upper bounds are higher — QQQ $10k→$3.99M — but **anchor on the walk-forward numbers.** They are what an investor re-optimizing each January would actually have earned.

---

## Contents

- [1. Recommendation (start here)](#1-recommendation-start-here)
- [2. Strategy](#2-strategy)
- [3. Methodology](#3-methodology)
- [4. Full-history grid search (in-sample upper bound)](#4-full-history-grid-search-in-sample-upper-bound)
- [5. Choosing the exit MA](#5-choosing-the-exit-ma)
- [6. The three selection rules — the core contribution](#6-the-three-selection-rules--the-core-contribution)
- [7. Walk-forward validation (the honest test)](#7-walk-forward-validation-the-honest-test)
- [8. Drawdown and tail risk](#8-drawdown-and-tail-risk)
- [9. Risk and design honesty](#9-risk-and-design-honesty)
- [10. Technical reference](#10-technical-reference)

---

## 1. Recommendation (start here)

The strategy never holds a leveraged ETF unconditionally: it buys dips only while the base index is above its 200-day moving average, and sells all leverage the moment price breaks back below the exit MA. Within that fixed premise, a grid search tunes the parameters, and one of three **selection rules** decides which passing combo to trade.

### Two usable variants per index

| | **Highest CAGR** (maximize growth) | **Balanced** (maximize return-per-drawdown) |
|---|---|---|
| Selection rule | top CAGR among survivors | top Calmar = CAGR / \|maxDD\| |
| Leverage it converges to | **3×** (TQQQ/UPRO), 100% | **2×** (QLD/SSO) + small base-stock cushion |
| QQQ walk-forward CAGR (2015–2026) | **33.7%** | **24.9%** |
| QQQ walk-forward worst year | −22.6% | **−18.2%** |
| QQQ full-history max drawdown | −55.9% | **−34.6%** |
| Who it's for | can stomach −30%+ years for max growth | wants to beat B&H with far gentler drawdowns |

Both beat QQQ buy-and-hold (19.4% walk-forward CAGR) decisively. **For "make money with manageable drawdowns," the Balanced 2× variant is the sweet spot** — +5.5pp over B&H with drawdowns closer to an unleveraged index than to a 3× ETF.

### Live parameters for calendar year 2026

Trained on 2003-01-02 → 2025-12-31. A row labeled *year N* was trained on data through Dec 31 of *N−1* and is traded during *N*.

| Index · variant | Entry | Drop | Exit | Buy% | Allocation |
|---|---|---|---|---|---|
| **QQQ · Highest CAGR** | 1.04×MA200 | 0.0% (any non-up day) | 1.01×MA200 | 100% | 100% TQQQ (3×) |
| **QQQ · Balanced** | 1.04×MA200 | 0.0% | 1.01×MA200 | 90% | 20% QQQ + QLD (2×) |
| **SPY · Highest CAGR** | 1.02×MA200 | 0.5% | 0.95×MA100 | 80% | 100% UPRO (3×) |
| **SPY · Balanced** | 1.05×MA200 | 0.0% | 0.94×MA100 | 20% | 10% SPY + SSO (2×) |

> **SPY caveat (read before trading SPY):** out-of-sample, *neither* SPY variant's annually re-optimized form reliably beats buy-and-hold (§7). SPY's edge is fragile. If you trade SPY at all, the Balanced variant is the defensible choice (it at least controls the worst year), but QQQ is the strategy's real home. **IWM is not recommended** — it failed out-of-sample.

### Re-optimize each January

```bash
# QQQ — produces both variants from one grid pass
python walkforward.py --preset QQQ --exit-ma 200 --select cagr,calmar --only-year <year>
# SPY
python walkforward.py --preset SPY --exit-ma 100 --select cagr,calmar --only-year <year>
```

Park idle cash in a T-bill ETF (SGOV/BIL): the strategy is fully in cash after every exit, and accruing the 13-week T-bill rate added **+0.6pp** to QQQ's walk-forward CAGR (33.7→34.3%) with a better worst year, at zero added risk (`--cash-yield`).

---

## 2. Strategy

**Why not just buy and hold a 3× ETF?** Two compounding problems: (1) *volatility decay* — daily leverage reset erodes value in choppy markets (QQQ −10% then +11.1% nets flat; TQQQ −30% then +33.3% nets −6.7%); (2) *bear markets are ruinous* — a buy-and-hold TQQQ investor lost >99% in 2000–2002 and ~95% in 2008. The strategy holds leverage **only during confirmed uptrends** and exits on trend break, cutting the catastrophic tail and avoiding chop.

**Buying.** (1) *Arm* when the base ETF closes above `entry × MA200`. (2) Once armed, a single-day drop of at least `drop_level` fires a buy. (3) First buy of a cycle optionally fills a base-stock position to `alloc_base`, then deploys `min(buy_pct × total, cash)` into leverage, split between 2× and 3× by `alloc_x2 / alloc_x3`. (4) Each further dip while armed adds leverage only.

> `drop_level = 0.0` means "buy on any day that doesn't close up." Negative values (tested, down to −1%) mean "buy even on a mildly up day." For QQQ the optimum is `0.0` — above its trend threshold, waiting for a dip is pure drag.

**Selling.** If price falls below `exit × exit_MA` while holding leverage: sell all 2×/3× to cash, trim the base position back to target once, and dis-arm until a fresh uptrend signal appears.

| Parameter | Meaning |
|---|---|
| `entry_signal` | arm above `MA200 × entry` |
| `drop_level` | min single-day drop to trigger a buy (negative = buy on mildly-up days) |
| `exit_signal` | exit below `exit_MA × exit` |
| `buy_pct` | fraction of portfolio deployed per buy, capped by cash |
| `alloc_base` | one-time base-ETF cushion (filled on first buy, trimmed on first exit) |
| `alloc_x2 / alloc_x3` | split of the leveraged tranche between 2× and 3× (sum to 1) |
| `exit_ma` | exit MA period: 50 / 100 / 200 (arming always uses MA200) |

---

## 3. Methodology

**Universe and window.** QQQ/QLD/TQQQ, SPY/SSO/UPRO, IWM/UWM/TNA, all from **2003-01-01** (a fair common floor: IWM launched 2000, the real 3× ETFs 2009–2010). The dot-com 2000–2002 period is excluded from optimization because the leveraged series there is 100% synthetic and extreme (§8 stress-tests it separately). Data through **2026-06-11**.

**Synthetic leveraged NAV.** Before a real ETF existed, its NAV is modeled as
`lev_ret = L·r − 0.5·(L²−L)·var₂₀ − MER/252`, where the MER drag applies **only** to the synthetic pre-inception period (real prices already embed fees). Synthetic series are stitched to real prices at inception. All prices are dividend-adjusted.

**Two tools, one engine.** `optimizer.py` *finds* (scans all combos on one window, keeps every result); `walkforward.py` *validates* (re-runs the search each year, keeps the chosen pick, backtests the schedule); `backtester.py` *measures* (one parameter set, full precision, authoritative for any cited number). All three share **`optimizer_core.py`** — one data pipeline, one backtest loop, one drawdown filter, one grid — so they cannot drift apart. (The annual re-optimizer in the `daily_signal` companion project runs the identical engine.)

**Grid v3 — 72,000 combinations.**

| Parameter | Values |
|---|---|
| `entry_signal` | 1.01, 1.02, 1.03, 1.04, 1.05, 1.06 |
| `drop_level` | −1.0%, −0.5%, 0.0%, 0.25%, 0.5%, 1.0%, 1.5%, 2.0% |
| `exit_signal` | 0.93, 0.94, 0.95, 0.97, 0.99, 1.00, 1.01, 1.02 |
| `buy_pct` | 10% … 100% (10 steps) |
| `alloc_base` | 0%, 10%, 20%, 30% |
| `alloc_x2` | 0%, 25%, 50%, 75%, 100% |

Constraint: `exit < entry`. The grid deliberately spans the full position-sizing range (up to 100%) and probes below the dip threshold (negative drops) so the optimizer's choices are interior, not pinned at an artificial edge.

**Drawdown filter.** A combo passes only if no calendar year from the ETF-inception cutoff onward (QQQ 2010, SPY/IWM 2009) lost more than **40%**. The filter is enforced only on real (post-inception) data, since synthetic pre-inception drawdowns are punishingly large and would eliminate good combos unfairly.

**Sharpe** uses the historical 13-week T-bill (^IRX) as a daily-varying risk-free rate, so it properly penalizes failing to beat cash in high-rate years.

---

## 4. Full-history grid search (in-sample upper bound)

These are hindsight-optimized over the entire 23-year sample — an upper bound, **not** a forward expectation (see §7 for the honest numbers). Per-index best by exit MA, Highest-CAGR rule:

| Index | MA200 | MA100 | MA50 | Chosen exit MA |
|---|---|---|---|---|
| QQQ | **29.1%** | 26.4% | 24.2% | MA200 |
| SPY | 22.8% | 23.0% | 25.2%* | MA100 |
| IWM | 13.8% | 12.6% | 10.7% | MA200 |

\* SPY MA50 is highest in-sample but collapses out-of-sample (§5); MA100 is chosen.

**Authoritative backtests of the chosen full-history configs (2003 → 2026-06-11, $10k):**

| Config | CAGR | B&H | Worst year | Max DD | Sharpe | Final |
|---|---|---|---|---|---|---|
| QQQ Highest-CAGR (3×, buy 100%) | **29.1%** | 16.2% | −35.0% (2005) | −55.9% | 0.78 | $3,987,903 |
| QQQ Balanced (2×+20% base, buy 90%) | 21.2% | 16.2% | **−18.8%** | **−34.6%** | 0.80 | $901,018 |
| SPY Highest-CAGR (3×, buy 80%) | 23.0% | 11.4% | −37.9% | −52.3% | 0.74 | $1,272,532 |
| SPY Balanced (2×+10% base, buy 20%) | 13.9% | 11.4% | **−21.5%** | **−26.7%** | 0.63 | $209,698 |

With a T-bill cash sleeve, QQQ Highest-CAGR rises to 29.7% ($4.47M); in an Ontario taxable account at a $100k salary it nets ~25.0% after-tax (TFSA/RRSP: untaxed).

![Parameter robustness heatmaps](results/optimizer/param_robustness_heatmap.png)

---

## 5. Choosing the exit MA

Arming always uses MA200 (the structural "confirmed bull market" signal). The *exit* MA is tuned per index, validated by walk-forward (annual re-opt, Highest-CAGR):

- **QQQ → MA200.** Walk-forward 33.7% (MA200) vs 32.9% (MA100): MA200 is slow enough to ignore normal bull-market volatility; faster exits cut profitable runs.
- **SPY → MA100.** Walk-forward 12.3% (MA100) vs 8.9% (MA200) vs 13.3% (MA50). MA100 is the balanced choice; MA200 exits too late through SPY's sharper breaks.
- **IWM → MA200**, but moot — IWM fails regardless.

MA50 is too reactive for QQQ/IWM (fires on routine pullbacks) and, while highest *in-sample* for SPY, does not generalize.

---

## 6. The three selection rules — the core contribution

The grid search finds thousands of passing combos per window. *Which one do you trade?* The earlier versions of this study always took **highest CAGR** — and that is exactly what overfits, because the highest-CAGR survivor is always the most aggressive one (buy 100%, 3×), sitting right against the −40% filter edge. Three rules were implemented and **each was validated out-of-sample**, not just compared in-sample:

1. **Highest CAGR** — top CAGR among survivors. Maximizes growth; accepts deep drawdowns.
2. **Balanced (Calmar)** — top `CAGR / |max drawdown|`. The best return-per-unit-of-pain. It has no hand-tuned knob, and it *naturally converges to 2× leverage plus a base-stock cushion* — it discovers lower leverage on its own as the efficient way to cut drawdown.
3. **maxDD-capped** — Highest-CAGR, but with a hard rule that rejects any combo whose real-data peak-to-trough drawdown exceeds 50%.

**What we learned about the maxDD cap (an honest negative result):** a hard in-sample drawdown ceiling barely helps. For QQQ it nudged buy 100%→90% in a few windows (+0.5pp walk-forward, same worst year); **for SPY it never bound at all** — SPY's MA100 exit keeps in-sample drawdowns just under 50%, yet it still lost −49.5% out-of-sample in 2022. **An in-sample drawdown cap cannot bound an out-of-sample tail.** The genuinely effective tail control is the Calmar rule (which moves to 2×), not the cap. The cap remains available (`--max-dd`) and documented, but it is not the recommended lever.

---

## 7. Walk-forward validation (the honest test)

Each January, the optimizer is re-run on all prior data only, the pick is frozen, and traded for the next 12 months. No look-ahead. **Fixed** = 2015 parameters frozen through 2026; **Expanding** = re-optimized every year.

### QQQ (MA200) — the strategy works, and re-optimization adds real edge

| Variant | Fixed | **Expanding** | B&H | Worst year (exp) | Sharpe (exp) |
|---|---|---|---|---|---|
| Highest CAGR | 23.0% | **33.7%** | 19.4% | −22.6% | 0.83 |
| Balanced (Calmar) | 23.7% | **24.9%** | 19.4% | **−18.2%** | **0.86** |
| maxDD-capped | 29.1% | 34.2% | 19.4% | −22.6% | 0.84 |

The Highest-CAGR schedule converges to `1.04 / 0.0 / 1.01 / buy 100%` and holds it unchanged from 2017 on — a stable, non-churning schedule. Annual re-optimization beats the frozen model by ~10pp, so it is worth doing. Balanced gives up ~9pp of CAGR for a materially gentler ride (best Sharpe, shallowest worst year).

### SPY (MA100) — the edge is fragile

| Variant | Fixed | **Expanding** | B&H | Worst year (exp) | Sharpe (exp) |
|---|---|---|---|---|---|
| Highest CAGR | 16.1% | **12.3%** ✗ | 13.7% | −49.5% | 0.45 |
| Balanced (Calmar) | 13.2% | **12.7%** ✗ | 13.7% | **−21.7%** | 0.56 |

**This is the key honest finding.** Annual re-optimization on SPY *underperforms buy-and-hold* out-of-sample. The mechanism: by ~2021 the training window is dominated by a long calm bull market, so the optimizer picks buy 100% — which then takes a −49.5% hit in 2022 (a year worse than anything in its training data). Only the *frozen* 2015 parameters (a conservative buy 50%, trained on a window that still included 2008) beat B&H. The Balanced rule rescues the *worst year* (−21.7% vs −49.5%) but not the return. **Verdict: do not annually re-optimize SPY on CAGR; treat SPY as marginal and prefer QQQ.**

### IWM (MA200) — not recommended

Expanding 6.2% vs 9.6% B&H (−3.4pp). Small-cap LETF decay and unstable parameters; disclosed for completeness only.

---

## 8. Drawdown and tail risk

> **Directly answering "would this prevent an 80% drawdown?" — No method here guarantees it; only lower leverage meaningfully shrinks it.**

Crisis backtests of the QQQ Highest-CAGR config (3×) and the worst case for the Balanced config (2×):

| Period | Highest-CAGR (3×) | Balanced (2×) |
|---|---|---|
| Dot-com 2000–2003 (100% synthetic) | CAGR −26.3%, worst −84.9%, **maxDD −92.1%** | CAGR −10.1%, worst −55.4%, **maxDD −76.8%** |
| GFC 2007–2010 | +24.7%, maxDD −38.8% | — |
| COVID 2019–2021 | +69.6%, Sharpe 1.20 | — |
| 2022 rate-hike 2021–2023 | +39.5% | — |

Three honest points:

1. **The strategy survives ordinary bears well** (it side-steps the GFC and excels in COVID/2022) because the MA200 exit moves it to cash. The catastrophic case is a *sustained, choppy secular bear* (dot-com), where repeated arm→buy→whipsaw-out cycles bleed capital, amplified by leverage.
2. **The 80%+ tail is outside all training data.** The optimizer trains on 2003-onward, which contains no dot-com-magnitude secular bear, so neither selection rule nor the maxDD cap is calibrated against it.
3. **Leverage is the only effective lever on the tail.** Moving from 3× to 2× (which the Balanced rule does automatically) cut the synthetic dot-com drawdown from −92% to −77%. To bound it further would require 2× exclusively, an explicit live circuit-breaker on portfolio drawdown, or simply not trading the strategy through a confirmed multi-year secular bear.

---

## 9. Risk and design honesty

- **Anchor on walk-forward, not full-history.** The 23-year backtest is hindsight-optimized. The 2015–2026 walk-forward (QQQ 33.7% Highest / 24.9% Balanced) is the realistic forward expectation.
- **SPY is marginal and IWM fails.** Stated plainly in §7. The strategy's durable edge is on QQQ.
- **The −40% filter is calendar-year, not max-drawdown.** A peak-to-trough spanning two years can exceed −40% while each year stays inside it; a hard maxDD cap on in-sample data does not fix this (§6).
- **3× requires stomaching −20% to −35% calendar years.** If you cannot, trade the Balanced 2× variant or stay unleveraged.
- **Taxes are the largest real cost.** In an Ontario taxable account at $100k salary, QQQ Highest-CAGR nets ~25% vs 29% pre-tax. Use TFSA → RRSP → taxable.
- **Synthetic-data dependence.** ~6–7 early years rely on the leverage-decay model; the dot-com numbers especially are approximations, not traded prices.

---

## 10. Technical reference

**Run the optimizer (one window, all combos):**
```bash
python optimizer.py --preset QQQ --exit-ma 200 --no-show
```

**Walk-forward both variants in one grid pass:**
```bash
python walkforward.py --preset QQQ --exit-ma 200 --select cagr,calmar \
  --start-year 2015 --end-year 2026 --no-show
```

**Selection / filter flags:** `--select cagr,calmar` (comma-separated, one grid pass yields all); `--max-dd 0.50` (hard real-period drawdown ceiling); `--dd-limit 0.40` (calendar-year filter); `--cash-yield` (T-bill sleeve); `--tie-tolerance` (CAGR-band worst-year refinement).

**Authoritative single-config backtest:**
```bash
python backtester.py --preset QQQ --start 2003-01-01 \
  --entry-signal 1.04 --drop-level 0.0 --exit-signal 1.01 \
  --buy-pct 1.0 --alloc-base 0 --alloc-x2 0 --alloc-x3 1 --no-show
```

**Repository layout:**
- `optimizer_core.py` — shared engine (data + synthetic NAV + MER, backtest loop, DD/maxDD filters, grids, parallel search). Single source of truth.
- `optimizer.py` — full-window grid-search CLI → `results/optimizer/{preset}/`
- `walkforward.py` — expanding-window validation → `results/walkforward/`
- `backtester.py` — authoritative single-config runner → `results/backtester/{preset}/`
- `param_heatmap.py` — robustness heatmaps
- `run_build.py` — reproduces the full result set (9 optimizers + multi-select walk-forwards)
- `results/README_DATA_LEDGER.md` — every figure in this paper with its source file

**Output filename tags:** `_ma{N}` (non-200 exit), `_grid{v}`, `_sel{rule}` (non-CAGR selection), `_maxdd{N}` (drawdown cap), `_dd{N}` (non-40% filter), `_cy` (cash yield). Variants never collide.
