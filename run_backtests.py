"""
run_backtests.py — run the backtester validation suite for the top combo
found by each production optimizer run (grid v3).

Reads results/optimizer/{P}/{P}_ma{MA}_*_gridv3_results.csv, takes the top
passing combo, and runs backtester.py for:
  - full history (2003 → today)
  - full history + --cash-yield
  - full history + --tax-ontario (QQQ/SPY only)
  - 2× variant (alloc_x2=1.0) for the leverage comparison
  - crisis windows: GFC 2007–2010, COVID 2019–2021, 2022 hike 2021–2023,
    dot-com 2000–2003

Usage: python run_backtests.py            (after the optimizers finish)
"""

import glob
import subprocess
import sys
from pathlib import Path

import pandas as pd

BASE = Path(__file__).parent

# Production exit-MA per preset; IWM kept for the disclosure section.
RUNS = [("QQQ", 200), ("SPY", 100), ("IWM", 200)]

CRISES = [
    ("2007-01-01", "2010-12-31"),   # GFC
    ("2019-01-01", "2021-12-31"),   # COVID
    ("2021-01-01", "2023-12-31"),   # 2022 rate-hike bear
    ("2000-01-01", "2003-12-31"),   # dot-com (synthetic, worst case)
]


def top_combo(preset: str, exit_ma: int) -> dict:
    pat = BASE / "results" / "optimizer" / preset
    hits = sorted(glob.glob(str(pat / f"{preset}_ma{exit_ma}_*_gridv3_results.csv")))
    if not hits:
        sys.exit(f"No optimizer CSV for {preset} ma{exit_ma} — run optimizer.py first.")
    df = pd.read_csv(hits[-1])
    p  = df[df["passed"]].sort_values("cagr", ascending=False).iloc[0]
    return p.to_dict()


def bt(preset, exit_ma, p, extra, label):
    cmd = [sys.executable, str(BASE / "backtester.py"),
           "--preset", preset, "--exit-ma", str(exit_ma),
           "--entry-signal", str(p["entry_signal"]),
           "--drop-level",   str(p["drop_level"]),
           "--exit-signal",  str(p["exit_signal"]),
           "--buy-pct",      str(p["buy_pct"]),
           "--alloc-base",   str(p["alloc_base"]),
           "--alloc-x2",     str(p["alloc_x2"]),
           "--alloc-x3",     str(round(1 - p["alloc_x2"], 4)),
           "--no-show"] + extra
    log = BASE / "results" / "logs" / f"bt_{preset}_ma{exit_ma}_{label}.log"
    print(f"  {label:24s} -> {log.name}")
    with open(log, "w", encoding="utf-8") as fh:
        subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, check=False)


def main():
    (BASE / "results" / "logs").mkdir(parents=True, exist_ok=True)
    for preset, exit_ma in RUNS:
        p = top_combo(preset, exit_ma)
        print(f"\n{preset} ma{exit_ma} winner: entry={p['entry_signal']} "
              f"drop={p['drop_level']} exit={p['exit_signal']} "
              f"buy={p['buy_pct']} base={p['alloc_base']} x2={p['alloc_x2']} "
              f"(CAGR {p['cagr']:.2f}%, worst {p['worst_ann_ret']:.2f}%)")

        bt(preset, exit_ma, p, ["--start", "2003-01-01"], "full")
        bt(preset, exit_ma, p, ["--start", "2003-01-01", "--cash-yield"], "full_cy")
        if preset in ("QQQ", "SPY"):
            bt(preset, exit_ma, p, ["--start", "2003-01-01",
                                    "--tax-ontario", "--salary", "100000"],
               "full_taxON")
            # 2× variant for the leverage comparison (same params, all-2×)
            p2 = dict(p, alloc_x2=1.0)
            bt(preset, exit_ma, p2, ["--start", "2003-01-01"], "full_2x")
        for start, end in CRISES:
            bt(preset, exit_ma, p, ["--start", start, "--end", end],
               f"crisis_{start[:4]}")
    print("\nAll backtests done. Summaries in results/backtester/, "
          "logs in results/logs/.")


if __name__ == "__main__":
    main()
