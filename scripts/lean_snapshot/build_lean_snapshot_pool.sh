#!/usr/bin/env bash
set -euo pipefail

# Build a pool of Firecracker Lean snapshots for true multi-VM concurrency.
#
# Why this exists:
# - Firecracker snapshots are path-sensitive (notably the host-side vsock UDS path).
# - You cannot safely restore multiple VMs concurrently from a single snapshot directory.
# - A "pool" is just N independent snapshot directories, one per concurrent worker.
#
# This script is intentionally non-destructive: it creates a new pool directory and never overwrites.
#
# Prereqs:
# - `fc_create_lean_snapshot_vsock.sh` must work on this machine.
# - You will be prompted for sudo (the snapshot builder uses loop-mount/chroot/etc).
#
# Usage:
#   ./build_lean_snapshot_pool.sh --count 4
#   ./build_lean_snapshot_pool.sh --count 4 --pool-id mypool
#
# After building, it prints a pasteable `FIRECRACKER_SNAPSHOT_DIRS=...` line.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SNAP_ROOT="${LEAN_SNAPSHOT_ROOT:-$REPO_ROOT/lean_snapshot}"
BUILDER="$SCRIPT_DIR/fc_create_lean_snapshot_vsock.sh"

COUNT=2
POOL_ID=""
ALIAS_BASE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --count)
      COUNT="${2:-}"
      shift 2
      ;;
    --pool-id)
      POOL_ID="${2:-}"
      shift 2
      ;;
    --alias-base)
      ALIAS_BASE="${2:-}"
      shift 2
      ;;
    -h|--help)
      sed -n '1,120p' "$0"
      exit 0
      ;;
    *)
      echo "ERROR: unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "${COUNT}" ]] || ! [[ "${COUNT}" =~ ^[0-9]+$ ]] || [[ "${COUNT}" -lt 1 ]]; then
  echo "ERROR: --count must be a positive integer" >&2
  exit 2
fi

if [[ ! -d "$SNAP_ROOT" ]]; then
  echo "ERROR: missing snapshot root: $SNAP_ROOT" >&2
  exit 2
fi
if [[ ! -x "$BUILDER" ]]; then
  echo "ERROR: missing/executable snapshot builder: $BUILDER" >&2
  exit 2
fi

TS="$(date +%Y%m%d-%H%M%S)"
if [[ -z "$POOL_ID" ]]; then
  # Keep this short since Firecracker's API socket path is limited (SUN_LEN ~108 bytes).
  POOL_ID="pool-${TS}"
fi

POOL_DIR="$SNAP_ROOT/pool/$POOL_ID"
if [[ -e "$POOL_DIR" ]]; then
  echo "ERROR: pool dir already exists: $POOL_DIR" >&2
  exit 2
fi
mkdir -p "$POOL_DIR"

USER_ALIAS_BASE="${LEAN_SNAPSHOT_ALIAS_BASE:-$ALIAS_BASE}"
if [[ -z "$USER_ALIAS_BASE" ]]; then
  if [[ -d /var/tmp && -w /var/tmp ]]; then
    USER_ALIAS_BASE="/var/tmp/lsnap-${USER:-user}"
  else
    USER_ALIAS_BASE="/tmp/lsnap-${USER:-user}"
  fi
fi

ensure_alias() {
  local alias_base="$1"
  local target="$2"

  if [[ -e "$alias_base" && ! -L "$alias_base" ]]; then
    echo "ERROR: alias path exists but is not a symlink: $alias_base" >&2
    exit 2
  fi
  if [[ -L "$alias_base" ]]; then
    local alias_target
    alias_target="$(readlink -f "$alias_base" || true)"
    local real_target
    real_target="$(readlink -f "$target" || true)"
    if [[ -n "$alias_target" && -n "$real_target" && "$alias_target" != "$real_target" ]]; then
      echo "ERROR: alias points elsewhere: $alias_base -> $alias_target (expected $real_target)" >&2
      echo "Set LEAN_SNAPSHOT_ALIAS_BASE or --alias-base to a different path, or fix the symlink." >&2
      exit 2
    fi
    return 0
  fi
  ln -s "$target" "$alias_base"
}

ensure_alias "$USER_ALIAS_BASE" "$SNAP_ROOT"

ALIAS_POOL_DIR="$USER_ALIAS_BASE/pool/$POOL_ID"
echo "[pool] building ${COUNT} snapshots under: $POOL_DIR"
echo "[pool] alias path (used for build/restore): $ALIAS_POOL_DIR"
echo "[pool] note: you will likely be prompted for sudo by the builder"

SNAP_DIRS=()
for i in $(seq 0 $((COUNT - 1))); do
  # Use the alias path for SNAP_DIR to avoid Firecracker UDS path length limits.
  WORKER_REL="pool/$POOL_ID/worker$(printf '%02d' "$i")"
  WORKER_DIR_REAL="$SNAP_ROOT/$WORKER_REL"
  WORKER_DIR_ALIAS="$USER_ALIAS_BASE/$WORKER_REL"
  mkdir -p "$WORKER_DIR_REAL"
  echo
  echo "[pool] worker $i/$((COUNT - 1)) -> $WORKER_DIR_REAL"
  echo "[pool]   alias -> $WORKER_DIR_ALIAS"
  SNAP_DIR="$WORKER_DIR_ALIAS" "$BUILDER"
  SNAP_DIRS+=("$WORKER_DIR_ALIAS")
done

joined="$(IFS=, ; echo "${SNAP_DIRS[*]}")"
echo
echo "=== Pool Build Complete ==="
echo "Pool dir: $POOL_DIR"
echo "Pool alias: $ALIAS_POOL_DIR"
echo
echo "Pasteable env:"
echo "export FIRECRACKER_SNAPSHOT_DIRS=\"$joined\""
echo
echo "Example load test:"
echo "CONCURRENCY=$COUNT ITERS=60 FIRECRACKER_SNAPSHOT_DIRS=\"$joined\" python scripts/atp_firecracker_repl_load_test.py"
