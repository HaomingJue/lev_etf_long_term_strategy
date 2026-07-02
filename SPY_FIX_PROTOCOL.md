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

## Amendments

*(none yet)*
