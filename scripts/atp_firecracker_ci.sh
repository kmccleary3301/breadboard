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
  FIRST_SNAPSHOT_DIR="$(awk -F',' '{print $1}' <<<"$SNAPSHOT_DIRS_CANON" | xargs)"
  if [[ -z "$FIRST_SNAPSHOT_DIR" ]]; then
    echo "FIRECRACKER_SNAPSHOT_DIRS is set but empty after parsing" >&2
    exit 2
  fi
  export FIRECRACKER_SNAPSHOT="${FIRECRACKER_SNAPSHOT:-$FIRST_SNAPSHOT_DIR/lean.snap}"
  export FIRECRACKER_SNAPSHOT_MEM="${FIRECRACKER_SNAPSHOT_MEM:-$FIRST_SNAPSHOT_DIR/lean.mem}"
  export FIRECRACKER_SNAPSHOT_VSOCK="${FIRECRACKER_SNAPSHOT_VSOCK:-$FIRST_SNAPSHOT_DIR/vsock.sock}"
  export FIRECRACKER_ROOTFS="${FIRECRACKER_ROOTFS:-$FIRST_SNAPSHOT_DIR/rootfs.ext4}"
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
fi

echo "[ATP] Firecracker REPL smoke..."
python -m pytest -q tests/test_sandbox_firecracker.py::test_firecracker_repl_smoke

echo "[ATP] Firecracker REPL benchmark..."
python scripts/atp_firecracker_repl_bench.py

if [ "${ATP_REPL_BENCH_VALIDATE:-}" = "1" ]; then
  echo "[ATP] Validating bench summary..."
  SUMMARY_PATH="${ATP_REPL_BENCH_SUMMARY_PATH:-${BENCH_SUMMARY_PATH:-${PWD}/artifacts/atp_firecracker_repl_bench.json}}"
  python - <<PY
import json
from pathlib import Path

path = Path("${SUMMARY_PATH}")
if not path.exists():
    raise SystemExit(f"bench summary missing: {path}")
data = json.loads(path.read_text(encoding="utf-8"))
for key in ("p50_ms", "p95_ms", "budget_pass", "iters"):
    if key not in data:
        raise SystemExit(f"bench summary missing key: {key}")
print(f"[ATP] Bench summary ok: {path}")
PY
fi

if [ "${ATP_REPL_LOAD_VALIDATE:-}" = "1" ]; then
  echo "[ATP] Firecracker REPL load smoke..."
  if [[ -n "$SNAPSHOT_DIRS_RAW" ]]; then
    SNAP_DIR_COUNT="$(awk -F',' '{print NF}' <<<"$SNAPSHOT_DIRS_CANON")"
    DEFAULT_CONCURRENCY="$SNAP_DIR_COUNT"
    if [[ "$DEFAULT_CONCURRENCY" -gt 2 ]]; then
      DEFAULT_CONCURRENCY=2
    fi
    if [[ "$DEFAULT_CONCURRENCY" -lt 1 ]]; then
      DEFAULT_CONCURRENCY=1
    fi
  else
    DEFAULT_CONCURRENCY=1
  fi
  CONCURRENCY="${ATP_REPL_LOAD_CONCURRENCY:-$DEFAULT_CONCURRENCY}" \
  ITERS="${ATP_REPL_LOAD_ITERS:-20}" \
  python scripts/atp_firecracker_repl_load_test.py
fi
