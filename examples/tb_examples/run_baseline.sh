#!/usr/bin/env bash
set -euo pipefail

# Baseline (no evolution, no skills) on Terminal-Bench 2.0
#
# Runs tasks without evolution and without any skills (vanilla prompt only).
#
# Usage:
#   bash examples/tb_examples/run_baseline.sh <RUN_NAME>
#   bash examples/tb_examples/run_baseline.sh Mar25_baseline --workers 8
#   bash examples/tb_examples/run_baseline.sh Mar25_test --limit 2
#   nohup bash examples/tb_examples/run_baseline.sh Mar25_baseline &

RUN_NAME="${1:?Usage: $0 <RUN_NAME> [--workers N] [--limit N] [--exclude task1,task2]}"
shift

LOG_DIR="logs/baseline_${RUN_NAME}"
WORK_DIR="/tmp/baseline_${RUN_NAME}"
WORKERS=6
LIMIT=""
EXCLUDE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --workers)  WORKERS="$2"; shift 2 ;;
        --limit)    LIMIT="$2";   shift 2 ;;
        --exclude)  EXCLUDE="$2"; shift 2 ;;
        *) echo "Unknown flag: $1"; exit 1 ;;
    esac
done

LIMIT_FLAG=""
if [[ -n "$LIMIT" ]]; then
    LIMIT_FLAG="--limit $LIMIT"
fi

EXCLUDE_FLAG=""
if [[ -n "$EXCLUDE" ]]; then
    EXCLUDE_FLAG="--exclude $EXCLUDE"
fi

mkdir -p "$LOG_DIR"

echo "============================================================"
echo "  Baseline (No Evolution, No Skills): ${RUN_NAME}"
echo "  Workspace:  ${WORK_DIR}"
echo "  Logs:       ${LOG_DIR}"
echo "  Workers:    ${WORKERS}"
echo "  Tasks:      ${LIMIT:-all}"
echo "  Exclude:    ${EXCLUDE:-none}"
echo "============================================================"
echo ""

echo ">>> Running tasks (no evolution, no skills)"
UV_CACHE_DIR=/tmp/uv_cache uv run python examples/tb_examples/batch_evolve_terminal.py \
  --solver react \
  --no-evolve \
  --no-skills \
  $EXCLUDE_FLAG \
  --workers "$WORKERS" \
  $LIMIT_FLAG \
  --work-dir "$WORK_DIR" \
  --log-dir "$LOG_DIR" \
  --output "$LOG_DIR/results.jsonl" \
  --errors "$LOG_DIR/errors.jsonl" \
  2>&1 | tee "$LOG_DIR/batch.log"

echo ""
echo "============================================================"
echo "  Baseline complete: ${RUN_NAME}"
echo "  Results: ${LOG_DIR}/results.jsonl"
echo "============================================================"
