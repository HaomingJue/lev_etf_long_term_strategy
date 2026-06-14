# README data ledger (data through 2026-06-11)

Every figure the README cites, with its source. Working scratch — not the README.
All walk-forward CAGRs are the expanding-window (annual re-opt) OOS numbers,
2015–2026, computed from the saved per-window grids (`results/walkforward/grids/`).

## Full-history optimizer winners — Highest CAGR (results/optimizer/{P}/*_summary.txt)
| Preset | exit MA | entry | drop | exit | buy | base | lev | CAGR | B&H |
|---|---|---|---|---|---|---|---|---|---|
| QQQ | 200 | 1.04 | 0.0   | 1.01 | 100% | 0   | 3× | 29.11% | 16.16 |
| QQQ | 100 | 1.02 | −0.01 | 0.94 | 100% | 0   | 3× | 26.44% | 16.16 |
| QQQ | 50  | 1.01 | −0.01 | 0.95 | 100% | 30% | 3× | 24.16% | 16.16 |
| SPY | 100 | 1.02 | 0.005 | 0.95 | 80%  | 0   | 3× | 22.97% | 11.34 |
| SPY | 200 | 1.02 | 0.01  | 0.95 | 100% | 0   | 3× | 22.83% | 11.34 |
| SPY | 50  | 1.02 | 0.01  | 0.93 | 100% | 0   | 3× | 25.22% | 11.34 |
| IWM | 200 | 1.04 | 0.015 | 0.95 | 100% | 0   | 3× | 13.75% | 10.37 |

## Full-history authoritative backtests (results/backtester/{P}/*_summary.txt)
| Config | CAGR | worst yr | maxDD | final |
|---|---|---|---|---|
| QQQ Highest CAGR (buy100 3×)            | 29.11% | −35.0% | −55.9% | $3,987,903 |
| QQQ Balanced maxDD≤50 (buy90 base20 3×) | 28.12% | −28.2% | −49.7% | $3,332,673 |
| QQQ Conservative (buy80 base30 2×)      | 20.75% | −22.3% | −34.2% | $830,467 |
| SPY Highest CAGR (buy80 3×)             | 22.97% | −39.0% | −52.3% | $1,272,533 |
| SPY buy-cap50 (buy50 3×)                | 22.60% | −33.3% | −52.7% | $1,187,808 |
| SPY Conservative (buy90 base30 2×)      | 15.03% | −17.8% | −36.5% | $266,498 |
- QQQ Highest +cash-yield 29.73%; +Ontario tax $100k salary 25.09% (tax paid $437k)

## Walk-forward 2015–2026 (Fixed | Expanding | B&H) — the honest test
| Run | Fixed | Expanding | B&H | verdict |
|---|---|---|---|---|
| QQQ Highest CAGR        | 22.97 | **33.69** | 19.42 | re-opt WINS (+14.3pp), worst −22.6 |
| QQQ Balanced maxDD≤50   | 29.11 | **34.23** | 19.42 | beats uncapped OOS; recommended |
| QQQ buy-cap50           | 22.97 | 33.30 | 19.42 | structural cap, ~same as Highest |
| QQQ Conservative (2×)   | 27.01 | 27.01 | 19.42 | gentlest, worst −18.8 |
| QQQ Highest +cash-yield | 23.62 | 34.32 | 19.42 | T-bill sleeve +0.6pp |
| SPY Highest CAGR        | 16.12 | 12.32 | 13.73 | expanding FAILS |
| SPY maxDD≤50            | 16.12 | 12.32 | 13.73 | cap is SLACK pre-2022 → = Highest, FAILS |
| SPY buy-cap50           | 16.12 | **14.68** | 13.73 | only rule that beats B&H (+0.95pp), worst −40.5 |
| SPY Conservative (2×)   | 14.16 | 13.44 | 13.73 | gentle (worst −17.8) but ~matches B&H |
| IWM Highest CAGR        |  2.83 |  6.17 |  9.59 | FAILS OOS → not recommended |
| IWM maxDD≤50 / buycap50 / Calmar | — | 5.18 / 1.03 / 6.42 | 9.59 | every rule fails |

KEY findings:
- QQQ schedule converges to 1.04/0/1.01/buy100% (Highest) from 2017 → stable.
- maxDD-cap rescues QQQ (regularizes buy100→buy90+base, beats uncapped OOS) but
  is SLACK for SPY: SPY's highest-CAGR combo has only ~−35% real maxDD on every
  pre-2022 window, so a 40–55% cap never binds → picks the same combo that loses
  −49% in 2022. An in-sample cap cannot bound an unseen tail.
- SPY's only OOS-positive lever is the STRUCTURAL buy-cap (≤50%), reproducing the
  conservative sizing of the original study — and it still only edges B&H.

## Crisis stress tests — README §8 (crisis_analysis.py → results/crisis/)
Period return · maxDD within each window, all 3 QQQ variants + SPY Balanced (buy-cap):
- Dot-com 2000–2003 (100% synthetic): Aggr −70.7% · −92.1% | Bal −64.7% · −89.5% |
  Cons(2×) −36.8% · −76.6% | SPY-bal +10.8% · −43.9%  → leverage is the tail lever; buy-cap survives.
- GFC 2007–2009: Aggr +64.7% · −38.8% | Bal +52.8% · −39.7% | Cons +38.5% · −32.4% | SPY +4.7% · −51.5%.
- COVID 2020: Aggr +103.9% · −51.8% | Bal +97.0% · −45.5% | Cons +65.0% · −32.6% | SPY +22.6% · −41.9%.
- 2022 hike Nov'21–mid'23: Aggr +8.9% · −38.0% | Bal +7.0% · −35.9% | Cons +4.7% · −26.7% | SPY −16.3% · −48.5%.
Figures: results/crisis/crisis_{dotcom,gfc,covid,ratehike}.png (2-panel equity log + underwater).

## Trade frequency — README §1 (crisis_analysis.py, cross-checks backtester Total trades)
Full history 2003–2026 (~23.4 yr): QQQ Aggr 43 (22 buy/21 exit, 1.8/yr) | Bal 50 (2.1/yr) |
Cons 47 (2.0/yr) | SPY-bal 59 (49 buy/10 exit, 2.5/yr). Busiest single year ever = 5 (SPY 7 in 2020).
→ "~2 trades/year, mostly HOLD" message in §1 + daily_signal README.

## Naming (2026-06-13/14): tiers = Aggressive (cagr) / Balanced (maxdd50 QQQ, buycap50 SPY) / Conservative (calmar).
Display labels only; internal keys (QQQ_highest_cagr etc.) + select rule "cagr" unchanged. Applied to
both lev_etf + daily_signal READMEs/code. §3–§7 keep "highest-CAGR" as the *rule* name (ties to Aggressive in §6).

## Heatmap
- results/optimizer/param_robustness_heatmap.png (param_heatmap.py; QQQ MA200 + SPY MA100)
