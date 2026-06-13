"""
run_build.py — sequential driver for the two-variant (Highest-CAGR + Balanced)
result rebuild. Single parent process so the whole run is easy to stop:

    taskkill /F /T /PID <this process's PID>   (Windows; /T kills the tree)

Steps:
  A. Optimizers (full history) for any preset/exit-MA whose CSV is missing the
     calmar column — adds max_dd + calmar. Skips CSVs already up to date.
  B. Multi-select walk-forwards (--select cagr,calmar) for the production
     configs QQQ ma200 and SPY ma100: one grid search per window, both the
     Highest-CAGR and Balanced schedules + Phase-2 backtests derived from it.

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


def _has_calmar(preset: str, ma: int) -> bool:
    import glob
    hits = glob.glob(str(ROOT / "results" / "optimizer" / preset /
                        f"{preset}_ma{ma}_*_gridv3_results.csv"))
    if not hits:
        return False
    with open(hits[-1], "r", encoding="utf-8") as fh:
        return "calmar" in fh.readline()


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

    # Phase A — optimizers (only those missing the calmar column)
    print("\n=== PHASE A: optimizers (add maxDD/calmar where missing) ===", flush=True)
    for preset in ("QQQ", "SPY", "IWM"):
        for ma in (200, 100, 50):
            if _has_calmar(preset, ma):
                print(f"  skip {preset} ma{ma} (calmar column present)", flush=True)
                continue
            run([PY, "optimizer.py", "--preset", preset, "--exit-ma", str(ma),
                 "--no-show"], f"opt_{preset}_ma{ma}.log")

    # Phase B — multi-select walk-forwards for the production configs.
    # Two feasible sets per config (one grid pass each):
    #   (1) standard -40% calendar-year filter        -> cagr + calmar
    #   (2) standard filter + hard -50% maxDD ceiling  -> cagr + calmar (capped)
    # The maxDD ceiling is a genuinely different feasible set, so it is a
    # separate grid pass; within each pass both selection rules are derived.
    print("\n=== PHASE B: multi-select walk-forwards (cagr + calmar) ===", flush=True)
    for preset, ma in (("QQQ", 200), ("SPY", 100)):
        run([PY, "walkforward.py", "--preset", preset, "--exit-ma", str(ma),
             "--select", "cagr,calmar", "--start-year", "2015",
             "--end-year", "2026", "--no-show"],
            f"wf_{preset}_ma{ma}_multi.log")
        run([PY, "walkforward.py", "--preset", preset, "--exit-ma", str(ma),
             "--select", "cagr,calmar", "--max-dd", "0.50", "--start-year", "2015",
             "--end-year", "2026", "--no-show"],
            f"wf_{preset}_ma{ma}_maxdd50.log")

    print(f"\n[{time.strftime('%H:%M:%S')}] BUILD DONE", flush=True)


if __name__ == "__main__":
    main()
