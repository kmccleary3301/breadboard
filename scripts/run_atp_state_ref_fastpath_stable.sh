#!/usr/bin/env bash
set -euo pipefail

# Stable state_ref benchmark protocol:
# 1) warmup pass (discard output for comparisons)
# 2) measured pass (with explicit burn-in inside the run)
#
# Required:
#   FIRECRACKER_SNAPSHOT_DIRS=/snap0,/snap1,... (preferred), or FIRECRACKER_SNAPSHOT=/snapX/lean.snap
#
# Optional:
#   RUN_TAG=c4
#   CONCURRENCY=4
#   WARMUP_ITERS=3
#   MEASURE_ITERS=10
#   MEASURE_BURN_IN_ITERS=2
#   MIN_RECOMMENDED_ITERS=10
#   REPL_TIMEOUT=120

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BENCH_SCRIPT="$REPO_ROOT/scripts/atp_state_ref_fastpath_bench.py"
PRECHECK_SCRIPT="$REPO_ROOT/scripts/atp_snapshot_preflight.py"
POOL_HEALTH_SCRIPT="$REPO_ROOT/scripts/atp_snapshot_pool_health.py"
ARTIFACT_DIR="$REPO_ROOT/artifacts"

if [[ ! -f "$BENCH_SCRIPT" ]]; then
  echo "Missing benchmark script: $BENCH_SCRIPT" >&2
  exit 1
fi
if [[ ! -f "$PRECHECK_SCRIPT" ]]; then
  echo "Missing preflight script: $PRECHECK_SCRIPT" >&2
  exit 1
fi
if [[ ! -f "$POOL_HEALTH_SCRIPT" ]]; then
  echo "Missing pool health script: $POOL_HEALTH_SCRIPT" >&2
  exit 1
fi

if [[ -z "${FIRECRACKER_SNAPSHOT_DIRS:-}" && -z "${FIRECRACKER_SNAPSHOT:-}" ]]; then
  echo "Set FIRECRACKER_SNAPSHOT_DIRS or FIRECRACKER_SNAPSHOT before running." >&2
  exit 1
fi

SNAPSHOT_DIRS_CANON="${FIRECRACKER_SNAPSHOT_DIRS:-}"
if [[ -n "$SNAPSHOT_DIRS_CANON" && "$SNAPSHOT_DIRS_CANON" != *,* ]]; then
  SNAPSHOT_DIRS_CANON="${SNAPSHOT_DIRS_CANON//:/,}"
  export FIRECRACKER_SNAPSHOT_DIRS="$SNAPSHOT_DIRS_CANON"
fi

echo "=== Snapshot preflight ==="
python "$PRECHECK_SCRIPT" --json

if [[ "${ATP_SNAPSHOT_POOL_PROBE:-0}" == "1" && -n "${FIRECRACKER_SNAPSHOT_DIRS:-}" ]]; then
  echo "=== Snapshot pool restore probe ==="
  python "$POOL_HEALTH_SCRIPT" \
    --snapshot-dirs "$SNAPSHOT_DIRS_CANON" \
    --probe-restore \
    --min-healthy "${ATP_SNAPSHOT_POOL_MIN_HEALTHY:-1}" \
    --json
fi

RUN_TAG="${RUN_TAG:-c4}"
WARMUP_ITERS="${WARMUP_ITERS:-3}"
MEASURE_ITERS="${MEASURE_ITERS:-10}"
MEASURE_BURN_IN_ITERS="${MEASURE_BURN_IN_ITERS:-2}"
MIN_RECOMMENDED_ITERS="${MIN_RECOMMENDED_ITERS:-10}"
REPL_TIMEOUT="${REPL_TIMEOUT:-120}"
CONCURRENCY="${CONCURRENCY:-}"

if [[ "$MEASURE_ITERS" -lt "$MIN_RECOMMENDED_ITERS" ]]; then
  echo "MEASURE_ITERS=${MEASURE_ITERS} is below MIN_RECOMMENDED_ITERS=${MIN_RECOMMENDED_ITERS}." >&2
  echo "Increase MEASURE_ITERS or explicitly lower MIN_RECOMMENDED_ITERS for non-baseline experiments." >&2
  exit 1
fi

if [[ -z "$CONCURRENCY" ]]; then
  if [[ -n "${FIRECRACKER_SNAPSHOT_DIRS:-}" ]]; then
    CONCURRENCY="$(awk -F',' '{print NF}' <<<"$SNAPSHOT_DIRS_CANON")"
  else
    CONCURRENCY=1
  fi
fi

mkdir -p "$ARTIFACT_DIR"
TS="$(date +%Y%m%d-%H%M%S)"
WARMUP_JSON="$ARTIFACT_DIR/atp_state_ref_fastpath_${RUN_TAG}_${TS}_warmup.json"
MEASURE_JSON="$ARTIFACT_DIR/atp_state_ref_fastpath_${RUN_TAG}_${TS}_measured.json"

echo "=== Warmup pass (discarded for comparisons) ==="
CONCURRENCY="$CONCURRENCY" \
ITERS="$WARMUP_ITERS" \
BURN_IN_ITERS=0 \
REPL_TIMEOUT="$REPL_TIMEOUT" \
MIN_RECOMMENDED_ITERS="$MIN_RECOMMENDED_ITERS" \
ATP_STATE_REF_BENCH_SUMMARY_PATH="$WARMUP_JSON" \
python "$BENCH_SCRIPT"

echo
echo "=== Measured pass (report this output) ==="
CONCURRENCY="$CONCURRENCY" \
ITERS="$MEASURE_ITERS" \
BURN_IN_ITERS="$MEASURE_BURN_IN_ITERS" \
REPL_TIMEOUT="$REPL_TIMEOUT" \
MIN_RECOMMENDED_ITERS="$MIN_RECOMMENDED_ITERS" \
ATP_STATE_REF_BENCH_SUMMARY_PATH="$MEASURE_JSON" \
python "$BENCH_SCRIPT"

echo
echo "Warmup summary:  $WARMUP_JSON"
echo "Measured summary: $MEASURE_JSON"

# Optional next step:
# Compare measured summaries across runs (measured-to-measured only) to avoid warm-cache skew.
