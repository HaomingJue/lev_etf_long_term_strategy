# README data ledger (v3 grid, data through 2026-06-11)

Every figure the rewritten README will cite, with its source file. Working
scratch — not the README itself.

## Full-history optimizer winners (results/optimizer/{P}/*_gridv3_summary.txt)
| Preset | exit MA | entry | drop | exit | buy | base | x2 | CAGR | worst yr | B&H |
|---|---|---|---|---|---|---|---|---|---|---|
| QQQ | 200 | 1.04 | 0.0 | 1.01 | 100% | 0 | 0 | 29.11% | −32.9 (train) | 16.16 |
| QQQ | 100 | 1.02 | −0.01 | 0.94 | 100% | 0 | 0 | 26.44% | −50.0 | 16.16 |
| QQQ | 50  | 1.01 | −0.01 | 0.95 | 100% | 30% | 0 | 24.16% | −46.3 | 16.16 |
| SPY | 100 | 1.02 | 0.005 | 0.95 | 80% | 0 | 0 | 22.97% | −39.0 | 11.34 |
| SPY | 200 | 1.02 | 0.01 | 0.95 | 100% | 0 | 0 | 22.83% | −34.2 | 11.34 |
| SPY | 50  | 1.02 | 0.01 | 0.93 | 100% | 0 | 0 | 25.22% | −32.6 | 11.34 |
| IWM | 200 | 1.04 | 0.015 | 0.95 | 100% | 0 | 0 | 13.75% | −29.1 | 10.37 |

## QQQ ma200 full-history backtester (HEADLINE, 2003-01→2026-06-11)
Source: results/backtester/QQQ/QQQ_2003-2026_entry1.04_exit1.01_drop0.0_buy1.0_b0_x20_ma200_summary.txt
- CAGR 29.11% | B&H 16.16% | edge +12.95pp | final $3,987,903 | Sharpe 0.78 | 43 trades
- Worst year −35.03% (**2005**, sideways chop — NOT 2022) | Max DD −55.85%
- Variants: +cash-yield 29.73% ($4.47M, worst −34.1, DD −53.9, Sharpe 0.79)
            +Ontario tax $100k salary 24.99% ($1.86M, edge +8.83pp)
            2× (QLD) 21.87% ($1.03M, worst −23.8, DD −39.4) — the leverage tradeoff
- Yearly highlights: 2003 +94%, 2005 −35%, 2008 −16.4% (vs QQQ −41.7%),
  2022 −22.6% (vs QQQ −32.6%), 2017 +118%, 2020 +108%

## Crisis stress tests (buy 100%, v3 winner params)
- QQQ dot-com 2000–2003 (100% synthetic): **CAGR −26.3%, worst yr −84.9%, maxDD −92.1%**
  → THE critical disclosure: buy-100% is catastrophic in a secular bear.
- QQQ GFC 2007–2010: CAGR +24.7%, worst −16.4%, maxDD −38.8% (vs QQQ B&H +6.6%)
- QQQ COVID 2019–2021: +69.6% CAGR, Sharpe 1.20
- QQQ 2022 hike 2021–2023: +39.5% CAGR
- IWM GFC 2007–2010: +16.9% CAGR, maxDD −51.2%

## Walk-forward 2015–2026 (Fixed | Expanding | B&H) — results/logs/wf_*.log
| Run | Fixed | Expanding | B&H | verdict |
|---|---|---|---|---|
| QQQ ma200 | 22.97 | **33.69** | 19.42 | re-opt WINS (+14.3pp), worst −22.6, Sharpe 0.84 |
| QQQ ma200 +cy | 23.62 | **34.32** | 19.42 | T-bill sleeve helps |
| QQQ ma200 tiebreak | 27.60 | 31.99 | 19.42 | tiebreak slightly worse than plain |
| QQQ ma100 | 32.18 | 32.85 | 19.42 | both good, ma200 better worst-yr |
| SPY ma100 | 16.12 | 12.32 | 13.73 | expanding FAILS (−1.4pp) |
| SPY ma100 +cy | 16.52 | 12.84 | 13.73 | cy doesn't fix it |
| SPY ma100 tiebreak | 15.90 | 12.88 | 13.73 | tiebreak doesn't rescue |
| SPY ma200 | 16.68 | 8.90 | 13.73 | expanding fails worse |
| SPY ma50 | 17.87 | 13.26 | 13.73 | fixed best; expanding ~flat |
| IWM ma200 | 2.83 | 6.17 | 9.59 | FAILS OOS → not recommended |

QQQ schedule converges to 1.04/0/1.01/buy100% from 2017, unchanged → stable.
SPY re-opt failure mechanism: by ~2021 training window is calm-bull-dominated →
optimizer picks buy 100% → −49.5% SPY in 2022 (breaches −40% filter OOS).
Fixed 2015 params = buy 50% (trained on 2003–14 incl. 2008) → survives.

## PENDING: SPY rescue experiments (run_spy_experiments.sh, ~3.3h)
- SPY ma100 v3cap (buy ≤60%)   → results/logs/wf_SPY_ma100_v3cap.log
- SPY ma100 v3 dd-limit 0.30   → results/logs/wf_SPY_ma100_dd30.log
- SPY ma50  v3cap              → results/logs/wf_SPY_ma50_v3cap.log
- SPY ma50  v3 dd-limit 0.30   → results/logs/wf_SPY_ma50_dd30.log
Determines final SPY recommendation + daily_signal GRID_VERSION/selection.
