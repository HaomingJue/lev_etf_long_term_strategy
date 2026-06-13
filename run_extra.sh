#!/bin/bash
# Follow-up walk-forwards after run_all.sh:
#  - SPY ma50: OOS test of the surprise full-history winner
#  - tie-break selection rule (QQQ ma200, SPY ma100): does picking the
#    safest near-tied leader beat plain top-CAGR out-of-sample?
cd "$(dirname "$0")"
mkdir -p results/logs

wf() {  # preset exit_ma extra_args log_tag
  echo "=== $(date +%H:%M:%S) walkforward $1 ma$2 $3 ==="
  python walkforward.py --preset "$1" --exit-ma "$2" $3 \
    --start-year 2015 --end-year 2026 --no-show \
    > "results/logs/wf_$1_ma$2$4.log" 2>&1 || echo "FAILED: wf $1 ma$2 $3"
}

wf SPY 50  ""                      ""
wf SPY 100 "--tie-tolerance 0.01"  "_tiebreak"
wf QQQ 200 "--tie-tolerance 0.01"  "_tiebreak"
echo "=== $(date +%H:%M:%S) EXTRA RUNS DONE ==="
