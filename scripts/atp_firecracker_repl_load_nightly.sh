#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PRECHECK_SCRIPT="$REPO_ROOT/scripts/atp_snapshot_preflight.py"
POOL_HEALTH_SCRIPT="$REPO_ROOT/scripts/atp_snapshot_pool_health.py"

if [[ ! -f "$PRECHECK_SCRIPT" ]]; then
  echo "Missing preflight script: $PRECHECK_SCRIPT" >&2
  exit 2
fi

SNAPSHOT_DIRS_RAW="${FIRECRACKER_SNAPSHOT_DIRS:-}"
SNAPSHOT_DIRS_CANON="$SNAPSHOT_DIRS_RAW"
if [[ -n "$SNAPSHOT_DIRS_CANON" && "$SNAPSHOT_DIRS_CANON" != *,* ]]; then
  SNAPSHOT_DIRS_CANON="${SNAPSHOT_DIRS_CANON//:/,}"
fi
if [[ -n "$SNAPSHOT_DIRS_RAW" ]]; then
  export FIRECRACKER_SNAPSHOT_DIRS="$SNAPSHOT_DIRS_CANON"
  echo "[ATP] Snapshot preflight (pool mode)..."
  python "$PRECHECK_SCRIPT" --json
  if [[ "${ATP_SNAPSHOT_POOL_PROBE:-0}" == "1" ]]; then
    if [[ ! -f "$POOL_HEALTH_SCRIPT" ]]; then
      echo "Missing pool health script: $POOL_HEALTH_SCRIPT" >&2
      exit 2
    fi
    echo "[ATP] Snapshot pool restore probe..."
    python "$POOL_HEALTH_SCRIPT" \
      --snapshot-dirs "$SNAPSHOT_DIRS_CANON" \
      --probe-restore \
      --min-healthy "${ATP_SNAPSHOT_POOL_MIN_HEALTHY:-1}" \
      --json
  fi
  SNAP_DIR_COUNT="$(awk -F',' '{print NF}' <<<"$SNAPSHOT_DIRS_CANON")"
else
  REQ_VARS=(
    FIRECRACKER_SNAPSHOT
    FIRECRACKER_SNAPSHOT_MEM
    FIRECRACKER_SNAPSHOT_VSOCK
    FIRECRACKER_ROOTFS
  )
  for var in "${REQ_VARS[@]}"; do
    if [ -z "${!var:-}" ]; then
      echo "Missing required env: $var" >&2
      exit 2
    fi
  done
  echo "[ATP] Snapshot preflight (single dir mode)..."
  python "$PRECHECK_SCRIPT" --json
  SNAP_DIR_COUNT=1
fi

CONCURRENCY="${ATP_REPL_LOAD_CONCURRENCY:-1}"
ITERS="${ATP_REPL_LOAD_ITERS:-40}"
SUMMARY_PATH="${ATP_REPL_LOAD_SUMMARY_PATH:-${PWD}/artifacts/atp_firecracker_repl_load_$(date +%Y%m%d).json}"

if [ "${CONCURRENCY}" -gt "${SNAP_DIR_COUNT}" ]; then
  echo "[ATP] CONCURRENCY=${CONCURRENCY} exceeds available snapshot dirs (${SNAP_DIR_COUNT})." >&2
  exit 2
fi

if [ -z "$SNAPSHOT_DIRS_RAW" ] && [ "${CONCURRENCY}" -gt 1 ]; then
  echo "[ATP] CONCURRENCY>1 is not supported with a single snapshot dir (vsock UDS path is snapshot-sensitive)." >&2
  echo "[ATP] Use CONCURRENCY=1, or set FIRECRACKER_SNAPSHOT_DIRS with one snapshot dir per worker." >&2
  exit 2
fi

echo "[ATP] Nightly Firecracker REPL load..."
CONCURRENCY="${CONCURRENCY}" \
ITERS="${ITERS}" \
ATP_REPL_LOAD_SUMMARY_PATH="${SUMMARY_PATH}" \
python scripts/atp_firecracker_repl_load_test.py

echo "[ATP] Load summary: ${SUMMARY_PATH}"
