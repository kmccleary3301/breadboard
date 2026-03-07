#!/usr/bin/env bash
set -euo pipefail

# Helper to:
# - ensure a short, user-writable alias path exists for `./lean_snapshot`
# - select a snapshot (defaults to latest fc_lean_snap_ready-*)
# - run ATP Firecracker REPL checks (smoke + bench) against that snapshot
#
# Why:
# - Firecracker's Unix-domain socket paths are length-limited (~108 bytes).
# - Snapshots are path-sensitive; restoring is most reliable via a stable short alias.
#
# Usage:
#   ./activate_lean_snapshot.sh --print-exports            # print export lines for sourcing/eval
#   ./activate_lean_snapshot.sh --ci                       # run BreadBoard ATP Firecracker CI checks
#   ./activate_lean_snapshot.sh --ci fc_lean_snap_ready-...# pick a specific snapshot id/dir name
#
# Apply exports in your current shell:
#   source <(./activate_lean_snapshot.sh --print-exports)
# or:
#   eval "$(./activate_lean_snapshot.sh --print-exports)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SNAP_ROOT="${LEAN_SNAPSHOT_ROOT:-$REPO_ROOT/lean_snapshot}"

PYTHON_CMD_DEFAULT="/home/querylake_manager/miniconda3/envs/ray_sce_test/bin/python"
PYTHON_CMD="${PYTHON_CMD:-$PYTHON_CMD_DEFAULT}"

MODE="${1:-}"
SNAP_ID="${2:-}"

if [[ "$MODE" != "--print-exports" && "$MODE" != "--ci" && "$MODE" != "" ]]; then
  # Allow calling with just a snapshot id: `./activate_lean_snapshot.sh fc_lean_snap_ready-...`
  SNAP_ID="$MODE"
  MODE="--print-exports"
fi
if [[ -z "$MODE" ]]; then
  MODE="--print-exports"
fi

if [[ ! -d "$SNAP_ROOT" ]]; then
  echo "ERROR: snapshot root missing: $SNAP_ROOT" >&2
  exit 2
fi

# Prefer a short stable alias in /var/tmp, but avoid root-owned shared names.
USER_ALIAS_BASE="${LEAN_SNAPSHOT_ALIAS_BASE:-}"
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
      echo "Set LEAN_SNAPSHOT_ALIAS_BASE to a different path, or fix the symlink." >&2
      exit 2
    fi
    return 0
  fi
  ln -s "$target" "$alias_base"
}

ensure_alias "$USER_ALIAS_BASE" "$SNAP_ROOT"

if [[ -z "$SNAP_ID" ]]; then
  SNAP_ID="$(ls -1td "$SNAP_ROOT"/fc_lean_snap_ready-* 2>/dev/null | head -n1 | xargs -n1 basename || true)"
fi
if [[ -z "$SNAP_ID" ]]; then
  echo "ERROR: no snapshot dirs found under $SNAP_ROOT (expected fc_lean_snap_ready-*)" >&2
  exit 2
fi

SNAP_DIR="$USER_ALIAS_BASE/$SNAP_ID"
if [[ ! -d "$SNAP_DIR" ]]; then
  echo "ERROR: snapshot dir not found via alias: $SNAP_DIR" >&2
  echo "Existing snapshots:" >&2
  ls -1 "$SNAP_ROOT" | sed -n '1,50p' >&2 || true
  exit 2
fi

FIRECRACKER_BIN="${FIRECRACKER_BIN:-}"
if [[ -z "$FIRECRACKER_BIN" ]]; then
  if [[ -x /usr/local/bin/firecracker ]]; then
    FIRECRACKER_BIN="/usr/local/bin/firecracker"
  else
    FIRECRACKER_BIN="$(command -v firecracker 2>/dev/null || true)"
  fi
fi
if [[ -z "$FIRECRACKER_BIN" ]]; then
  echo "ERROR: firecracker binary not found (set FIRECRACKER_BIN)" >&2
  exit 2
fi

exports=$(
  cat <<EOF
export LEAN_SNAPSHOT_ALIAS_BASE="$USER_ALIAS_BASE"
export SNAP_ID="$SNAP_ID"
export SNAP_DIR="$SNAP_DIR"
export FIRECRACKER_BIN="$FIRECRACKER_BIN"
export FIRECRACKER_EXEC_MODE="repl"
export FIRECRACKER_SNAPSHOT="$SNAP_DIR/lean.snap"
export FIRECRACKER_SNAPSHOT_MEM="$SNAP_DIR/lean.mem"
export FIRECRACKER_SNAPSHOT_VSOCK="$SNAP_DIR/vsock.sock"
export FIRECRACKER_ROOTFS="$SNAP_DIR/rootfs.ext4"
EOF
)

if [[ "$MODE" == "--print-exports" ]]; then
  echo "$exports"
  exit 0
fi

if [[ "$MODE" == "--ci" ]]; then
  if [[ ! -x "$PYTHON_CMD" ]]; then
    echo "ERROR: python not found/executable: $PYTHON_CMD" >&2
    echo "Set PYTHON_CMD=/path/to/python" >&2
    exit 2
  fi
  eval "$exports"
  cd "$REPO_ROOT"
  echo "[activate] Using snapshot: $SNAP_DIR"
  "$PYTHON_CMD" -m pytest -q tests/test_sandbox_firecracker.py::test_firecracker_repl_smoke
  "$PYTHON_CMD" scripts/atp_firecracker_repl_bench.py
  echo "[activate] CI checks OK"
  exit 0
fi

echo "ERROR: unexpected mode: $MODE" >&2
exit 2
