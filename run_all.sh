#!/bin/bash
# Full v3-grid result rebuild — 2026-06-12.
# Phase A: 9 full-history optimizers (3 indices x 3 exit MAs)
# Phase B: 5 expanding-window walk-forwards
cd "$(dirname "$0")"
mkdir -p results/logs

for P in QQQ SPY IWM; do
  for MA in 200 100 50; do
    echo "=== $(date +%H:%M:%S) optimizer $P ma$MA ==="
    python optimizer.py --preset "$P" --exit-ma "$MA" --no-show \
      > "results/logs/opt_${P}_ma${MA}.log" 2>&1 || echo "FAILED: opt $P ma$MA"
  done
done
echo "=== $(date +%H:%M:%S) optimizers done ==="

wf() {
  echo "=== $(date +%H:%M:%S) walkforward $1 ma$2 ==="
  python walkforward.py --preset "$1" --exit-ma "$2" \
    --start-year 2015 --end-year 2026 --no-show \
    > "results/logs/wf_$1_ma$2.log" 2>&1 || echo "FAILED: wf $1 ma$2"
}
wf QQQ 200
wf SPY 100
wf QQQ 100
wf SPY 200
wf IWM 200
echo "=== $(date +%H:%M:%S) ALL DONE ==="
