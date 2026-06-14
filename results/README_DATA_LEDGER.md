# README data ledger (data through 2026-06-11)

Every figure the README cites, with its source. Working scratch — not the README.
All walk-forward CAGRs are the expanding-window (annual re-opt) OOS numbers,
2015–2026, computed from the saved per-window grids (`results/walkforward/grids/`).

**Engine (2026-06-14):** base sleeve is rebalanced to `alloc_base` on EVERY trade
(topped up on each buy, trimmed on each exit); a single lev buy is capped at
`(1 − alloc_base)` of the portfolio. The grid prunes `buy_pct > 1 − alloc_base`
duplicates → **61,200 combos** (was 72,000). Only base>0 picks moved; base=0
combos (Aggressive, SPY buy-cap) are bit-identical to before.

## Full-history optimizer winners — Highest CAGR (results/optimizer/{P}/*_summary.txt)
| Preset | exit MA | entry | drop | exit | buy | base | lev | CAGR | B&H |
|---|---|---|---|---|---|---|---|---|---|
| QQQ | 200 | 1.04 | 0.0   | 1.01 | 100% | 0   | 3× | 29.11% | 16.16 |
| QQQ | 100 | 1.02 | −0.01 | 0.94 | 100% | 0   | 3× | 26.44% | 16.16 |
| QQQ | 50  | 1.02 | −0.01 | 0.95 | 100% | 0   | 3× | 24.09% | 16.16 |
| SPY | 100 | 1.02 | 0.005 | 0.95 | 80%  | 0   | 3× | 22.97% | 11.37 |
| SPY | 200 | 1.02 | 0.01  | 0.95 | 100% | 0   | 3× | 22.83% | 11.37 |
| SPY | 50  | 1.02 | 0.01  | 0.93 | 100% | 0   | 3× | 25.22% | 11.37 |
| IWM | 200 | 1.04 | 0.015 | 0.95 | 100% | 0   | 3× | 13.75% | 10.37 |
| IWM | 100 | 1.02 | 0.015 | 0.94 | 100% | 0   | 3× | 12.56% | 10.37 |
| IWM | 50  | 1.01 | 0.0025| 0.94 | 70%  | 30% | 3× | 11.27% | 10.37 |

## Full-history authoritative backtests (results/backtester/{P}/*_summary.txt)
| Config | CAGR | worst yr | maxDD | final |
|---|---|---|---|---|
| QQQ Aggressive (buy100 3×)              | 29.11% | −35.0% | −55.9% | $3,987,903 |
| QQQ Balanced maxDD≤50 (buy90 base10 3×) | 28.16% | −31.9% | −51.8% | $3,356,876 |
| QQQ Conservative (buy80 base20 2×)      | 20.91% | −19.1% | −33.4% | $856,189 |
| SPY Aggressive (buy100 3×, MA50)        | 25.22% | −31.5% | −52.1% | $1,947,757 |
| SPY buy-cap50 (buy20 3×, MA50)          | 24.49% | −36.5% | −51.5% | $1,700,103 |
| SPY Conservative (buy90 base10 2×, MA50)| 17.43% | −21.4% | −36.8% | $432,313 |
- QQQ Aggressive +cash-yield 29.73% (full-hist); +Ontario tax $100k salary ~25.1%.

## Walk-forward 2015–2026 (Fixed | Expanding | B&H) — the honest test
| Run | Fixed | Expanding | B&H | verdict |
|---|---|---|---|---|
| QQQ Aggressive          | 22.97 | **33.69** | 19.42 | re-opt WINS (+14.3pp), worst −22.6 |
| QQQ Balanced maxDD≤50   | 29.11 | **34.76** | 19.42 | beats uncapped OOS; recommended (worst −22.6) |
| QQQ buy-cap50           | 22.97 | 33.30 | 19.42 | structural cap, dominated by maxDD≤50 |
| QQQ Conservative (2×)   | 26.68 | 26.98 | 19.42 | gentlest, worst −18.4 |
| QQQ Aggressive +cash-yield | — | 34.32 | 19.42 | T-bill sleeve +0.6pp (Balanced +cy 35.36) |
| SPY Aggressive (ma50)   | 17.87 | 13.26 | 13.73 | expanding FAILS |
| SPY maxDD≤50 (ma50)     | 17.87 | 13.26 | 13.73 | cap SLACK pre-2022 → = Aggressive, FAILS |
| SPY buy-cap50 (ma50)    | 17.87 | **16.70** | 13.73 | ONLY rule that beats B&H (+3.0pp), worst −44 |
| SPY buy-cap50 (ma100)   | — | 14.68 | 13.73 | weaker than ma50 (+1.0pp) → ma50 wins |
| SPY buy-cap50 (ma200)   | — | 11.01 | 13.73 | FAILS → confirms ma50 |
| SPY Conservative (2×, ma50) | 10.99 | 12.24 | 13.73 | now FAILS B&H (was 14.0), worst −38 |
| IWM Aggressive          | — | 6.17 | 9.59 | FAILS OOS → not recommended |
| IWM maxDD≤50 / buycap50 / Calmar | — | 4.91 / 1.03 / 7.02 | 9.59 | every rule fails |

KEY findings (unchanged by the base-rebalance + cap):
- Exit MA settled: QQQ→MA200, SPY→MA50, IWM→fails on every MA. SPY does NOT pass on
  a slower MA — buy-cap OOS is ma50 16.70 > ma100 14.68 > ma200 11.01 (fail).
- maxDD-cap rescues QQQ (regularizes buy100→buy90+10% base, beats uncapped OOS 34.76)
  but is SLACK for SPY (highest-CAGR combo ~−35% real maxDD pre-2022 → never binds).
- SPY's only OOS-positive lever is the STRUCTURAL buy-cap (base=0, untouched by the
  engine change). Under the maintained-base engine the SPY Calmar 2× no longer beats
  B&H (converges to buy90 2×, 12.24% / −38% worst) → SPY ships one tradeable variant.

## Crisis stress tests — README §8 (crisis_analysis.py → results/crisis/)
Period return · maxDD within each window, all 3 QQQ variants + SPY Balanced (buy-cap):
- Dot-com 2000–2003 (100% synthetic): Aggr −70.7% · −92.1% | Bal −65.6% · −90.2% |
  Cons(2×) −31.0% · −74.8% | SPY-bal +10.8% · −43.9%  → leverage is the tail lever; buy-cap survives.
- GFC 2007–2009: Aggr +64.7% · −38.8% | Bal +58.4% · −38.4% | Cons +43.1% · −29.2% | SPY +4.7% · −51.5%.
- COVID 2020: Aggr +103.9% · −51.8% | Bal +100.7% · −48.7% | Cons +67.1% · −33.2% | SPY +22.6% · −41.9%.
- 2022 hike Nov'21–mid'23: Aggr +8.9% · −38.0% | Bal +8.0% · −36.8% | Cons +5.9% · −26.5% | SPY −16.3% · −48.5%.
Figures: results/crisis/crisis_{dotcom,gfc,covid,ratehike}.png (2-panel equity log + underwater).

## Trade frequency — README §1 (crisis_analysis.py, cross-checks backtester Total trades)
Full history 2003–2026 (~23.4 yr): all three QQQ variants 43 (22 buy/21 exit, 1.8/yr) —
identical, since they trade on the same signals and differ only in sizing.
SPY-bal 59 (49 buy/10 exit, 2.5/yr). Busiest single year ever = 4 (SPY 7 in 2020).
→ "~2 trades/year, mostly HOLD" message in §1 + daily_signal README.

## Naming (2026-06-13/14): tiers = Aggressive (cagr) / Balanced (maxdd50 QQQ, buycap50 SPY) / Conservative (calmar).
Display labels only; internal keys (QQQ_highest_cagr etc.) + select rule "cagr" unchanged. Applied to
both lev_etf + daily_signal READMEs/code. §3–§7 keep "highest-CAGR" as the *rule* name (ties to Aggressive in §6).

## Heatmap
- results/optimizer/param_robustness_heatmap.png (param_heatmap.py; QQQ MA200 + SPY MA100)
