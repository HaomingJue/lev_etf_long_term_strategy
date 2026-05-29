"""
One-off helper: add the 2026 walk-forward row to the existing schedules.

Trains the optimizer on 2003-01-01 -> 2025-12-31 only for trade_year=2026.
Merges the result into the existing JSON schedules so the 2014-2025 rows
are preserved (avoids the ~30 min full rebuild).

  python add_2026_row.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from walkforward import load_full_data, build_param_schedule

HERE = Path(__file__).parent
OUT  = HERE / "results" / "walkforward"

JOBS = [
    # (preset, exit_ma, tie_tolerance, schedule_filename)
    ("QQQ", 200, 0.0,  "QQQ_param_schedule.json"),
    ("QQQ", 200, 0.01, "QQQ_param_schedule_tiebreak.json"),
    ("SPY", 100, 0.0,  "SPY_param_schedule_ma100.json"),
    ("SPY", 200, 0.0,  "SPY_param_schedule.json"),
]

for preset, exit_ma, tie_tol, fname in JOBS:
    sched_path = OUT / fname
    print(f"\n{'=' * 60}\n{preset} (exit MA{exit_ma}) -> {fname}\n{'=' * 60}")

    existing = json.loads(sched_path.read_text())
    print(f"  Existing years: {sorted(int(k) for k in existing.keys())}")
    if "2026" in existing:
        print(f"  2026 row already present — skipping")
        continue

    # Need data through 2025-12-31 for training; pull a buffer to be safe.
    df = load_full_data(preset, "2026-01-15")
    df_train_end = df[df.index.year <= 2025]
    print(f"  Training data: {df_train_end.index[0].date()} -> "
          f"{df_train_end.index[-1].date()}  ({len(df_train_end)} rows)")

    new_rows = build_param_schedule(
        preset, start_year=2026, end_year=2026, df_full=df,
        exit_ma=exit_ma, tie_tolerance=tie_tol,
    )

    if 2026 not in new_rows:
        print(f"  ERROR: no 2026 row produced for {preset}")
        continue

    existing["2026"] = new_rows[2026]
    sched_path.write_text(json.dumps(existing, indent=2))
    print(f"\n  2026 row: {json.dumps(new_rows[2026], indent=2)}")
    print(f"  Saved to {sched_path}")

print("\nDone.")
