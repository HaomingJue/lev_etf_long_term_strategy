# README data ledger

Every figure the README cites, with its source. Working scratch — not the README.
Data ends: QQQ runs **2026-06-11**; SPY protocol-era runs **2026-07-01** (disclosed
in README §3). All walk-forward CAGRs are expanding-window (annual re-opt) OOS
numbers computed from the saved per-window grids (`results/walkforward/grids/`).

**Engine (2026-06-14):** base sleeve rebalanced to `alloc_base` on EVERY trade; a
single lev buy capped at `(1 − alloc_base)`; grid prunes duplicates → **61,200 combos**.

**Protocol era (2026-07-02):** SPY's variant re-derived under the pre-registered
`SPY_FIX_PROTOCOL.md` (rules `struct`/`robust1`/`plateau`, frozen gates, two
documented amendments). SPY production = **struct · MA200**. The old MA50
buy-cap rows are kept below as the superseded record — README §4 uses them as
the overfitting case history.

## Full-history authoritative backtests — README §9 table (results/backtester/{P}/*_summary.txt)
| Config | CAGR | B&H | worst yr | maxDD | Sharpe | final |
|---|---|---|---|---|---|---|
| QQQ Aggressive (buy100 3×, MA200)        | 29.11% | 16.2% | −35.0% | −55.9% | 0.78 | $3,987,913 |
| QQQ Balanced maxDD≤50 (buy90 base10 3×)  | 28.16% | 16.2% | −31.9% | −51.8% | 0.78 | $3,356,876 |
| QQQ Conservative (buy80 base20 2×)       | 20.91% | 16.2% | −19.1% | −33.4% | 0.80 | $856,189 |
| **SPY Satellite struct (1.02/0.0025/0.97/buy30 3×, MA200)** | **21.68%** | 11.38% | **−35.7% (2022)** | **−57.5% (COVID)** | 0.69 | $1,004,372 |
- QQQ Aggressive +cash-yield 29.73%; +Ontario tax $100k salary ~25.1%.
- SPY source: `results/backtester/SPY/SPY_2003-2026_entry1.02_exit0.97_drop0.0025_buy0.3_b0_x20_ma200_summary.txt`.

## Walk-forward 2015–2026 — README §7 (Fixed | Expanding | B&H)
| Run | Fixed | Expanding | B&H | verdict |
|---|---|---|---|---|
| QQQ Aggressive            | 22.97 | **33.69** | 19.42 | re-opt WINS (+14.3pp), worst −22.6 |
| QQQ Balanced maxDD≤50     | 29.11 | **34.76** | 19.42 | recommended (worst −22.6) |
| QQQ Conservative (2×)     | 26.68 | 26.98 | 19.42 | gentlest, worst −18.4 |
| QQQ Aggressive +cash-yield | — | 34.32 | 19.42 | T-bill sleeve +0.6pp (Balanced +cy 35.36) |
| **SPY struct (MA200)**    | **18.23** | **16.35** | 13.75 | SHIPS (+2.6pp), worst −37.1 (2022); fixed model best |
| IWM (all rules)           | — | 6.17 / 4.91 / 1.03 / 7.02 | 9.59 | every rule fails → not recommended |
- SPY yearly: `SPY_walkforward_2015-2026_gridv3_selstruct_yearly.csv`
  (−20.5 / +25.8 / +71.4 / −15.3 / +44.9 / −8.9 / +98.6 / −37.1 / +41.5 / +63.6 / +15.6 / −11.0*).

## Protocol matrix — README §6 (walkforward --from-grids, 2026-07-01 data; CAGR · worst yr)
| Rule | MA200 | MA100 | MA50 |
|---|---|---|---|
| struct  | **16.35 · −37.1** ✓ | 14.82 · −38.6 | 17.44 · −41.5 |
| robust1 | 15.89 · −34.3 | 15.26 · −35.0 | 15.86 · −36.5 |
| plateau | 9.58 ✗ · −45.6 | 15.27 · −49.5 | 16.32 · −56.5 |
- B&H ≈ 13.73–13.75 per MA run. Cell gate (amended): ≥ B&H+1.5pp AND worst ≥ −40%.
- QQQ control (MA200, B&H 19.25): struct 27.78 | robust1 29.30 | plateau **33.05**
  (plateau converges to ~production Balanced combo → §5 validation).
- Fork sensitivity (S2, experiments/fork_sensitivity.py): struct·MA200 rank-2 +1.69pp,
  band 7.72pp PASS; robust1·MA200 rank-2 +0.68pp, band 4.91pp (ranks 3–4 below B&H).
- Stability (S1): struct·MA200 6 distinct sets /12 yrs (7/16) — FAILED, ships disclosed.

## Fresh-data F1 — README §8 (walkforward --start-year 2011, windows 2003-2010..2013 searched 2026-07-02)
- SPY struct·MA200 2011–2026: **19.80% vs 14.10% B&H (+5.69pp)**, worst −37.1%.
- Fresh years: 2011 −21.3 (B&H +0.9) | 2012 +29.1 (+16.0) | 2013 +118.5 (+32.3) | 2014 +38.0 (+13.5).
- Picks on fresh windows: 1.01–1.02 / 0.25–0.5% / 0.94–0.95 / buy 40% (v1-family, independently reproduced).
- Files: `SPY_walkforward_2011-2026_gridv3_selstruct_*`.

## §4 case-history numbers (the overfitting exhibit; current-data re-derivations, 2026-07-02 review)
- v1-style constrained selection at MA200: 15.85% (+2.1pp) — `SPY_walkforward_2015-2026_selv1emu_*`.
- Wide-grid argmax at MA200: 9.01% (−4.7pp); buycap40: 11.07%; buycap50: 11.11%.
- 2015 fork: argmax (drop −1.0%, in-sample 27.4 vs 26.0) → OOS −23.6 vs −12.1.
- 2023 fork: argmax (exit 0.99, in-sample 21.1 vs 20.5) → OOS +2.8 vs +42.2.

## Crisis stress tests — README §9 (crisis_analysis.py → results/crisis/, rerun 2026-07-02)
Period return · maxDD within each window, 3 QQQ variants + SPY Satellite (struct·MA200):
- Dot-com 2000–2003 (100% synthetic): Aggr −70.7 · −92.1 | Bal −65.6 · −90.2 |
  Cons(2×) −31.0 · −74.8 | **SPY-struct +34.9 · −34.7** (lone survivor).
- GFC 2007–2009: Aggr +64.7 · −38.8 | Bal +58.4 · −38.4 | Cons +43.1 · −29.2 | SPY-struct +26.3 · −41.4.
- COVID 2020: Aggr +103.9 · −51.8 | Bal +100.7 · −48.7 | Cons +67.1 · −33.2 | **SPY-struct −9.7 · −57.5** (its worst case).
- 2022 hike Nov'21–mid'23: Aggr +8.9 · −38.0 | Bal +8.0 · −36.8 | Cons +5.9 · −26.5 | SPY-struct −16.9 · −47.9.
- Hold-the-LETF rows (no timing): TQQQ dot-com −100, GFC −80.2 · −96.9, 2022 −46.9;
  UPRO dot-com −85.0 · −94.2, GFC −90.7 · −97.5, COVID +7.2 · −76.8, 2022 −31.3 · −63.9;
  full-hist TQQQ 24.6% · −96.9, UPRO 14.8% · −97.5.

## Trade frequency — README §1 (crisis_analysis.py, rerun 2026-07-02)
Full history (~23.4 yr): all three QQQ variants 43 trades (22 buy/21 exit, ~1.8/yr), identical signals.
**SPY-struct 58 (46 buy/12 exit, ~2.5/yr)**, busiest 2004 (5).

## Heatmap
- results/optimizer/param_robustness_heatmap.png (param_heatmap.py; QQQ MA200 + SPY MA100).

---

## SUPERSEDED (pre-protocol SPY record — kept as §4's case history; do not cite as production)

Full-history optimizer winners (Highest CAGR, per exit MA): QQQ 200/100/50 =
29.11/26.44/24.09; SPY 100/200/50 = 22.97/22.83/25.22; IWM 200/100/50 = 13.75/12.56/11.27.

Old SPY walk-forwards (2026-06-11 data): Aggressive ma50 13.26 FAIL | maxdd50 ma50
13.26 FAIL (slack) | **buy-cap50 ma50 16.70 (+3.0pp), worst −44 — the retired
production variant** | buycap50 ma100 14.68 | buycap50 ma200 11.01 | Calmar 2× ma50
12.24 FAIL. Old full-history: SPY buy-cap50 ma50 24.49%, maxDD −51.5. Old crisis row
(SPY buy-cap ma50): dot-com +10.8 · −43.9 | GFC +4.7 · −51.5 | COVID +22.6 · −41.9 |
2022 −16.3 · −48.5. Old trade count: 59 (49 buy/10 exit).
