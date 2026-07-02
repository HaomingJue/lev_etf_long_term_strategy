# SPY Fix — Pre-Registered Protocol

**Frozen on 2026-07-02, before running any candidate below** (except as disclosed in
"Prior knowledge"). Any deviation must be recorded in the Amendments section of this
file — nothing is silently re-decided after seeing results.

## 1. Motivation (what the 2026-07-02 review found)

The v2/v3 grid widening moved SPY's walk-forward from beating B&H to losing to it.
A code/data review found **no bug**: restricting each cached v3 grid to the v1 axes
(drop ≥ 0.5%, exit ≥ 0.95, buy ≤ 40%) reproduces the v1-era schedule byte-for-byte
and walks forward to +2.1pp over B&H at MA200 (+3.9pp at MA100) on current data.
The damage came from **aggressiveness-monotone axes** (`drop ≤ 0`, `buy_pct → 1.0`)
plus argmax selection breaking noise-level in-sample ties (≤ 1.4pp) toward picks with
catastrophic OOS penalties (2015: −12pp; 2023: −40pp). The shipped buycap rule fixes
only the buy axis: buycap40 at MA200 = 11.1% vs the v1-style 15.9%.

## 2. Prior knowledge disclosure (what has already been seen on 2015–2026 OOS)

Already observed before this protocol was frozen — and therefore **cannot count as
confirmation** for any candidate:

- v1-style constraints (≈ candidate `struct`): MA200 15.85%, MA100 17.64% (worst −31.8%).
- buycap40/50 at MA200: ≈ 11.1%. Full §5 matrix of the current README.
- All v1/v2-era published walk-forwards.

Candidates `robust1` and `plateau` have **never** been scored on any OOS window.
The exit-MA decision rule, acceptance gates, and stability gates below were frozen
before scoring **any** of the three candidates.

## 3. Candidate selection rules — exactly three, no additions

All operate per training window on the DD-filter survivors of the standard v3 grid.

1. **`struct`** — structural caps: survivors with `buy_pct ≤ 0.40` **and**
   `drop_level ≥ 0.0025`; rank by in-sample CAGR. *Justification (a priori): the
   strategy's premise is dip-buying; `drop ≤ 0` degenerates into always-buy, and the
   buy cap bounds per-signal exposure against unseen tails (README §6).*
2. **`robust1`** — noise-aware conservative tie-break: survivors with
   `drop_level ≥ 0.0` (premise floor: never buy a rally day); among those within
   **1.0pp** of the top in-sample CAGR, pick lowest `buy_pct`, then highest
   `drop_level`, then lowest `exit_signal`, then grid order. *Justification:
   in-sample margins under ~1pp are backtest noise (README §1); ties must not
   resolve toward aggression.*
3. **`plateau`** — robustness-first ranking: survivors with `drop_level ≥ 0.0`;
   rank by the **median CAGR of the axis-aligned ±1-step grid neighborhood**
   (self included; neighbors = all evaluated combos, pass or fail; missing/pruned
   neighbors excluded). *Justification: operationalizes §4's "optimum must sit on
   a plateau, not a spike" as a selector instead of a post-hoc chart.*

QQQ is the **control**: `robust1` and `plateau` applied to QQQ MA200 must stay within
3pp of the production Balanced walk-forward (34.8%); large degradation falsifies the
"one selection principle" claim. `struct` is *expected* to cost QQQ CAGR (its cap
binds); its QQQ run is reported descriptively, not as a gate.

## 4. Scoring and decision rules (frozen)

Walk-forward 2015–2026, expanding windows from the cached v3 grids, Phase 2 on
current data; strategy and B&H always scored on the identical window.

- **Cell pass** (rule × exit-MA): OOS CAGR ≥ B&H + 1.5pp **and** worst OOS calendar
  year ≥ −35%.
- **Rule pass**: its best MA cell passes, **and** the rule clears B&H (CAGR > B&H)
  on ≥ 2 of the 3 exit MAs. A rule that only works on one MA is treated as
  cell-picking, not a winner.
- **Production MA** for a passing rule: among its passing cells, shallowest worst
  year; tie → higher CAGR.
- **Between passing rules**: shallowest worst year on their production MA; tie →
  higher CAGR.

### Stability gates (must also pass)

- **S1 — parameter stability**: ≤ 4 distinct parameter sets across the 12 windows.
- **S2 — fork sensitivity**: rebuild the schedule from the rule's **rank-2** pick in
  every window; its walk-forward CAGR must still be ≥ B&H. The rank-1..5 band is
  reported; a band wider than 8pp CAGR is a fail regardless of the headline.

### Fresh-data gates

- **F1 — extended OOS**: walk-forward 2011–2026 (training windows 2003–2010 …) at
  the production MA must beat B&H by ≥ 0.5pp. (2011–2014 outcomes were never used
  in any prior selection decision.)
- **F2 — cross-index**: QQQ control as in §3 (gate for robust1/plateau only);
  IWM transfer reported descriptively (expected to improve, not required to pass).
- **F3 — regime check** (descriptive): chosen trigger geometry, unleveraged, on
  1950–2026 S&P via `experiments/regime_reliability.py` methodology.

### Verdict

- Some rule passes everything → it becomes SPY's production variant; README
  restructured per the agreed outline (overfitting section, selection before MA
  choice, stability + fresh-data sections).
- No rule passes → **SPY is demoted to not-recommended**, documented as a negative
  result alongside the VIX experiment. This outcome is acceptable and final for
  this study; no fourth rule will be invented against the same window.

## 5. Multiple-comparisons ledger

Every SPY cell ever scored against the 2015–2026 window across the project's life
(v1 study ~6, v2 re-validation ~4, v3 §5 matrix 15, review probes 5, this protocol 9)
totals **≈ 39 comparisons**. Readers must interpret any single cell's ±2–4pp edge
accordingly; this is why the gates above demand multi-MA consistency, fork
robustness, and untouched 2011–2014 data rather than one good number.

## Results (2026-07-02, same day, after freezing)

Walk-forward 2015–2026, cached v3 grids, Phase-2 data through 2026-07-01.
Cell gate = CAGR ≥ B&H + 1.5pp **and** worst year ≥ −35%.

| Cell | CAGR | edge vs B&H | worst yr | cell gate |
|---|---|---|---|---|
| MA200 · struct | 16.35% | +2.60pp | −37.1% | ✗ worst yr |
| **MA200 · robust1** | 15.89% | +2.14pp | −34.3% | **✓** |
| MA200 · plateau | 9.58% | −4.16pp | −45.6% | ✗ |
| MA100 · struct | 14.82% | +1.08pp | −38.6% | ✗ |
| MA100 · robust1 | 15.26% | +1.52pp | −35.0% | borderline |
| MA100 · plateau | 15.27% | +1.53pp | −49.5% | ✗ worst yr |
| MA50 · struct | 17.44% | +3.71pp | −41.5% | ✗ worst yr |
| MA50 · robust1 | 15.86% | +2.13pp | −36.5% | ✗ worst yr |
| MA50 · plateau | 16.32% | +2.58pp | −56.5% | ✗ worst yr |

Rule gates:

- **struct** — clears B&H on 3/3 MAs; S2 pass at MA200 (rank-2 +1.69pp, band
  7.72pp); but **no cell passes the −35% worst-year bar** (best −37.1%). FAIL.
- **robust1** — MA200 cell passes; S2 pass (rank-2 +0.68pp, band 4.91pp; note
  ranks 3–4 fall *below* B&H — the edge is thin across near-ties); but
  **S1 FAIL** (6 distinct param sets > 4) and **F2 FAIL** (QQQ control 29.30%
  vs production 34.8% = −5.5pp > 3pp; the 1pp-conservative tie-break
  over-corrects on QQQ). FAIL.
- **plateau** — passes the QQQ control best (33.05%, converges to the
  production-Balanced pick — QQQ's plateau is real); but on SPY it keeps
  selecting aggressive sizing and the 2023 exit-0.99 geometry: fails B&H
  outright at MA200 and the worst-year bar everywhere. FAIL.

## Verdict (per §4, binding)

**No candidate passes all gates → SPY is demoted to not-recommended.**
F1 (2011–2014 extended windows) was not run — no candidate survived the
cheaper gates. Positive by-products for the paper:

1. **QQQ's edge is not a selection artifact.** Even the fully capped `struct`
   variant (buy ≤ 40%, dip ≥ 0.25%) beats QQQ B&H by +8.5pp (27.78%), and the
   robustness-first `plateau` selector lands on essentially the production
   Balanced combo (33.05%). The QQQ recommendation is *strengthened*.
2. **SPY's edge, where it exists (+2–3pp), sits below the pre-registered
   risk bar** (−35% worst year) on every exit MA once selection is sane, and
   the one cell that clears it (robust1 · MA200) belongs to a rule that fails
   stability and the cross-index control.
3. The v1-era "SPY works modestly" result was real but rode the v1 grid's
   implicit caps; it does not survive being asked to prove itself under
   pre-registered gates.

## Amendments

### Amendment 1 — 2026-07-02 (post-results, owner decision)

**Change**: cell-gate worst-year bar relaxed from −35% to **−40%**. CAGR bar
(B&H + 1.5pp) and all other gates unchanged.

**Disclosure**: this is a **post-hoc goalpost move**, made after seeing the
results table above, to admit `struct · MA200` (16.35%, worst −37.1%) as a
"tradeable but high-risk" satellite. The README must state this plainly
wherever that variant is recommended. Under the amended bar:

- `struct · MA200` — cell ✓ (unique: MA100 fails the CAGR bar at +1.08pp,
  MA50 still fails worst-year at −41.5%), multi-MA ✓, S2 ✓.
  **S1 as written: FAIL** (6 distinct param sets > 4; they form one cluster —
  entry 1.02 from 2017 on, exit 0.97 from 2019 on, residual drift
  drop 0.0025↔0.005 / buy 0.4→0.3 — but the gate counts exact sets).
  **F1**: run after this amendment — **PASS**. Extended walk-forward
  2011–2026 (training windows 2003–2010 …, four never-before-searched
  windows): **19.80% vs 14.10% B&H = +5.69pp** (gate ≥ +0.5pp), worst year
  −37.1% (2022). The fresh 2011–2014 years scored +5.7pp of edge on picks
  from the same parameter family (entry 1.01–1.02, drop 0.25–0.5%, exit
  0.94–0.95×MA200, buy 40%) — incidentally reproducing the v1-era study's
  combos from data it never saw. (Fixing this run also surfaced and fixed a
  real `--from-grids` fallback crash in walkforward.py.)

  **Status**: `struct · MA200` passes every gate under Amendment 1 except
  **S1 as written** (6 distinct sets over 12 windows, 7 over 16, vs ≤ 4 —
  all one family, max one grid step of drift per axis per change). Ship /
  no-ship pending owner decision on S1 (Amendment 2 or final demotion).

### Amendment 2 — 2026-07-02 (owner decision)

**Change**: the verdict rule is amended to allow shipping `struct · MA200`
with **S1 recorded as FAILED**. S1 is *not* relabeled a pass and its metric is
*not* redefined; the README must disclose, wherever the variant is
recommended, that the rule re-tunes by micro-steps roughly every two years
within one parameter family (7 distinct sets over 16 windows; every set in
entry 1.01–1.02 · drop 0.25–0.5% · exit 0.94–0.97×MA200 · buy 30–40%).

## Final verdict (Amendments 1–2 applied)

**SPY ships `struct · MA200`** — highest-CAGR survivor with `buy ≤ 40%` and
`drop ≥ 0.25%`, exit on MA200 — as a **high-risk satellite** variant:
walk-forward 16.35% vs 13.75% B&H (2015–2026, worst year −37.1%), extended
19.80% vs 14.10% (2011–2026). Admitted under two documented post-hoc
amendments with S1 failed-and-disclosed; readers should weight it
accordingly. QQQ remains the production strategy, its selection-robustness
now *positively validated* by this protocol's control runs.
- `robust1` — unchanged verdict (fails F2/S1 regardless of this amendment).
- `plateau` — unchanged verdict.
