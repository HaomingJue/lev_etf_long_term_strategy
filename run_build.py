"""
run_build.py — sequential driver for the full result rebuild. Single parent
process so the whole run is easy to stop:

    taskkill /F /T /PID <this process's PID>   (Windows; /T kills the tree)

Steps:
  A. Optimizers (full history) for any preset/exit-MA whose CSV is missing the
     real-period maxDD column — adds max_dd + max_dd_real + calmar. Skips CSVs
     already up to date.
  B. Multi-select walk-forwards for the production configs (QQQ ma200, SPY
     ma100, IWM ma200): ONE grid search per window, with all selection-rule
     schedules (Highest-CAGR, maxDD-capped, structural buy-cap, Calmar) and
     their Phase-2 backtests derived from it. Each window's full grid is saved
     to results/walkforward/grids/, so any rule can be re-derived later with
     `walkforward.py --from-grids` (no re-search).

Run:  python run_build.py            (foreground, prints progress)
      python run_build.py 2>&1 | tee results/logs/build.log
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
PY   = sys.executable
LOGS = ROOT / "results" / "logs"
LOGS.mkdir(parents=True, exist_ok=True)


def _up_to_date(preset: str, ma: int) -> bool:
    import glob
    hits = glob.glob(str(ROOT / "results" / "optimizer" / preset /
                        f"{preset}_ma{ma}_*_results.csv"))
    if not hits:
        return False
    with open(hits[-1], "r", encoding="utf-8") as fh:
        return "max_dd_real" in fh.readline()


def run(cmd, log_name):
    log = LOGS / log_name
    print(f"\n[{time.strftime('%H:%M:%S')}] $ {' '.join(cmd)}\n    -> {log}",
          flush=True)
    with open(log, "w", encoding="utf-8") as fh:
        rc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT).returncode
    print(f"[{time.strftime('%H:%M:%S')}] exit {rc}", flush=True)
    if rc != 0:
        print(f"    !! FAILED — see {log}", flush=True)
    return rc


def main():
    print(f"PID {Path(__file__).name}: {__import__('os').getpid()}  "
          f"(stop with: taskkill /F /T /PID <pid>)", flush=True)

    # Phase A — optimizers (only those missing the real-period maxDD column)
    print("\n=== PHASE A: optimizers (add maxDD/max_dd_real/calmar where missing) ===",
          flush=True)
    for preset in ("QQQ", "SPY", "IWM"):
        for ma in (200, 100, 50):
            if _up_to_date(preset, ma):
                print(f"  skip {preset} ma{ma} (max_dd_real column present)", flush=True)
                continue
            run([PY, "optimizer.py", "--preset", preset, "--exit-ma", str(ma),
                 "--no-show"], f"opt_{preset}_ma{ma}.log")

    # Phase B — multi-select walk-forwards for the production configs. One grid
    # search per window; all selection-rule schedules + Phase-2 backtests are
    # derived from it, and every window's grid is saved for later --from-grids.
    SELECTS = "cagr,maxdd50,buycap50,calmar"
    print(f"\n=== PHASE B: multi-select walk-forwards ({SELECTS}) ===", flush=True)
    for preset, ma in (("QQQ", 200), ("SPY", 100), ("IWM", 200)):
        run([PY, "walkforward.py", "--preset", preset, "--exit-ma", str(ma),
             "--select", SELECTS, "--start-year", "2015",
             "--end-year", "2026", "--no-show"],
            f"wf_{preset}_ma{ma}_multi.log")

    print(f"\n[{time.strftime('%H:%M:%S')}] BUILD DONE", flush=True)


if __name__ == "__main__":
    main()
