# Leveraged ETF Long-Term Strategy

A trend-following, dip-buying strategy that uses leveraged ETFs (2× and 3×) to amplify returns on QQQ, SPY, and IWM. Includes a full-history optimizer that grid-searches 15,840 parameter combinations per index and ranks them by CAGR with a yearly drawdown filter.

---

## Strategy Logic

### Buy Cycle
1. **Arm** — price closes above `MA200 × entry_signal` (trend confirmed)
2. **Trigger** — while armed, same-day drop ≥ `drop_level` (buy the dip)
3. **First signal in cycle** — buy base ETF up to `alloc_base × portfolio`, then spend `buy_pct × portfolio` on leveraged ETFs split by `alloc_x2` / `alloc_x3`
4. **Subsequent signals** — spend `buy_pct × portfolio` on leveraged ETFs only (base already filled)

### Exit
- Price closes below `MA200 × exit_signal`
- Sell all 2× and 3× holdings → cash
- One-time trim of base if it exceeds `alloc_base × portfolio`
- Dis-arm: next cycle requires a fresh arm signal

### Synthetic Pre-Inception NAV
For dates before a real leveraged ETF existed, the NAV is simulated from the base ETF's daily returns using the standard leverage model:

```
lev_daily_ret = L × r  −  0.5 × (L² − L) × rolling_var20
```

The synthetic and real series are stitched seamlessly at the ETF's inception date.

---

## Repository Structure

```
backtester.py                        # interactive backtester (CLI)
leveraged_qqq_exploration/
    optimizer.py                     # QQQ grid search optimizer
    optimizer_results.csv            # all 15,840 combos, pass/fail
    optimizer_equity.png             # top 5 equity curves
    optimizer_scatter.png            # CAGR vs worst annual return
leveraged_spy_exploration/
    optimizer_spy.py                 # SPY grid search optimizer
    spy_optimizer_results.csv
    spy_optimizer_equity.png
    spy_optimizer_scatter.png
leveraged_iwm_exploration/
    optimizer.py                     # IWM grid search optimizer
    optimizer_results.csv
    optimizer_equity.png
    optimizer_scatter.png
```

---

## ETF Mapping

| Index | Base | 2× | 3× |
|---|---|---|---|
| NASDAQ-100 | QQQ | QLD (inception Jun 2006) | TQQQ (inception Feb 2010) |
| S&P 500 | SPY | SSO (inception Jun 2006) | UPRO (inception Jun 2009) |
| Russell 2000 | IWM | UWM (inception Jan 2007) | TNA (inception Nov 2008) |

---

## Optimizer Results Summary

Each optimizer ran 15,840 parameter combinations over full history (2003-01-01 to 2026-05-08, $10,000 starting capital). A combo **passes** if every calendar year from the filter year onward shows an annual return ≥ −40%.

| Index | Passing combos | Filter start year |
|---|---|---|
| QQQ | 15,817 / 15,840 | 2010 |
| SPY | 15,754 / 15,840 | 2009 |
| IWM | 15,674 / 15,840 | 2009 |

---

## Best Strategies

### QQQ — Best CAGR
| Parameter | Value |
|---|---|
| Entry signal | 1.03× MA200 |
| Drop level | 0.5% |
| Exit signal | 1.01× MA200 |
| Buy pct | 40% per signal |
| Allocation | 0% QQQ / 0% QLD / 100% TQQQ |
| **CAGR** | **21.31%** |
| Worst annual return | −38.58% |

```
python backtester.py --preset QQQ --entry-signal 1.03 --drop-level 0.005 --exit-signal 1.01 --buy-pct 0.4 --alloc-base 0.0 --alloc-x2 0.0 --alloc-x3 1.0
```

### QQQ — Balanced (CAGR vs drawdown)
| Parameter | Value |
|---|---|
| Entry signal | 1.04× MA200 |
| Drop level | 1.0% |
| Exit signal | 1.01× MA200 |
| Buy pct | 30% per signal |
| Allocation | 0% QQQ / 0% QLD / 100% TQQQ |
| **CAGR** | **19.51%** |
| Worst annual return | −24.77% |

```
python backtester.py --preset QQQ --entry-signal 1.04 --drop-level 0.010 --exit-signal 1.01 --buy-pct 0.3 --alloc-base 0.0 --alloc-x2 0.0 --alloc-x3 1.0
```

---

### SPY — Best CAGR
| Parameter | Value |
|---|---|
| Entry signal | 1.02× MA200 |
| Drop level | 0.5% |
| Exit signal | 0.95× MA200 |
| Buy pct | 30% per signal |
| Allocation | 0% SPY / 0% SSO / 100% UPRO |
| **CAGR** | **22.26%** |
| Worst annual return | −39.40% |

```
python backtester.py --preset SPY --entry-signal 1.02 --drop-level 0.005 --exit-signal 0.95 --buy-pct 0.3 --alloc-base 0.0 --alloc-x2 0.0 --alloc-x3 1.0
```

### SPY — Balanced (CAGR vs drawdown)
| Parameter | Value |
|---|---|
| Entry signal | 1.02× MA200 |
| Drop level | 1.0% |
| Exit signal | 0.95× MA200 |
| Buy pct | 40% per signal |
| Allocation | 0% SPY / 100% SSO / 0% UPRO |
| **CAGR** | **16.13%** |
| Worst annual return | −23.71% |

```
python backtester.py --preset SPY --entry-signal 1.02 --drop-level 0.010 --exit-signal 0.95 --buy-pct 0.4 --alloc-base 0.0 --alloc-x2 1.0 --alloc-x3 0.0
```

---

### IWM — Best CAGR
| Parameter | Value |
|---|---|
| Entry signal | 1.05× MA200 |
| Drop level | 1.5% |
| Exit signal | 0.95× MA200 |
| Buy pct | 30% per signal |
| Allocation | 10% IWM / 0% UWM / 100% TNA |
| **CAGR** | **10.23%** |
| Worst annual return | −27.13% |

```
python backtester.py --preset IWM --entry-signal 1.05 --drop-level 0.015 --exit-signal 0.95 --buy-pct 0.3 --alloc-base 0.1 --alloc-x2 0.0 --alloc-x3 1.0
```

### IWM — Balanced (CAGR vs drawdown)
| Parameter | Value |
|---|---|
| Entry signal | 1.04× MA200 |
| Drop level | 2.5% |
| Exit signal | 0.99× MA200 |
| Buy pct | 40% per signal |
| Allocation | 30% IWM / 0% UWM / 100% TNA |
| **CAGR** | **10.21%** |
| Worst annual return | −13.13% |

```
python backtester.py --preset IWM --entry-signal 1.04 --drop-level 0.025 --exit-signal 0.99 --buy-pct 0.4 --alloc-base 0.3 --alloc-x2 0.0 --alloc-x3 1.0
```

---

## Key Observations

- **QQQ and SPY significantly outperform IWM** in this strategy (~20%+ CAGR vs ~10%). Small-cap volatility does not translate to better leveraged returns with this approach.
- **The 3× ETF dominates** in nearly all top combos — the 2× allocation rarely appears in top-ranked results.
- **The high-CAGR combos carry heavy tail risk** (worst year near −40%). The balanced picks cut worst annual loss roughly in half at the cost of 1–6% CAGR.
- **SPY best CAGR edges out QQQ** (22.26% vs 21.31%), likely because the SPY optimizer's looser exit (0.95× MA200) keeps more capital deployed during recoveries.
- **IWM balanced pick is remarkably conservative** — worst annual return of only −13.13% while still delivering ~10% CAGR.

---

## Running the Optimizer

```bash
cd leveraged_qqq_exploration && python optimizer.py
cd leveraged_spy_exploration && python optimizer_spy.py
cd leveraged_iwm_exploration && python optimizer.py
```

Outputs per run: `optimizer_results.csv`, `optimizer_equity.png`, `optimizer_scatter.png`.

## Dependencies

```
pip install yfinance pandas numpy matplotlib tqdm
```
