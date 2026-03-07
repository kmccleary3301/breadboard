#!/usr/bin/env bash
set -euo pipefail

# Stable-location diagnostic runner for Lean/Mathlib warm snapshot creation.
#
# Why this exists:
# - Firecracker snapshots often capture absolute paths to drives and vsock UDS.
# - If you create a snapshot under `/tmp/...` and later move it, restoring can fail.
# - Building directly under `./lean_snapshot/...` makes the snapshot restorable without
#   relying on `/tmp` persisting.
#
# Usage:
#   1) Ensure sudo is unlocked in this shell: `sudo -v`
#   2) Run: `./run_lean_snapshot_diag_stable.sh`
#
# Output:
#   A new directory under `./lean_snapshot/` (created via a short alias path like
#   `/var/tmp/lsnap/` to keep Firecracker socket paths short) containing
#   `lean.snap`, `lean.mem`, `rootfs.ext4`, `data.ext4`, logs, etc.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$SCRIPT_DIR"

TS="$(date +%Y%m%d-%H%M%S)"

# Unix-domain socket paths (Firecracker API + vsock UDS) have a small max length
# (typically ~108 bytes). Paths under `/shared_folders/...` can exceed this and
# cause `curl --unix-socket ...` failures.
#
# To keep snapshots restorable while still storing artifacts in this repo folder,
# we create/use a short stable alias path and write SNAP_DIR under that alias.
#
# Default alias (short path to avoid UDS length limits):
# - `/var/tmp/lsnap` -> `$ROOT_DIR/lean_snapshot` (preferred; often more persistent than /tmp)
# - `/tmp/lsnap`     -> `$ROOT_DIR/lean_snapshot` (fallback)
# Override with:
# - `LEAN_SNAPSHOT_ALIAS_BASE=/some/short/path`
TARGET_BASE="${LEAN_SNAPSHOT_ROOT:-$REPO_ROOT/lean_snapshot}"
mkdir -p "$TARGET_BASE"

if [ -n "${LEAN_SNAPSHOT_ALIAS_BASE:-}" ]; then
  ALIAS_BASE="$LEAN_SNAPSHOT_ALIAS_BASE"
else
  # Pick a short writable base.
  if [ -d /var/tmp ] && [ -w /var/tmp ]; then
    ALIAS_BASE="/var/tmp/lsnap"
  else
    ALIAS_BASE="/tmp/lsnap"
  fi
fi

if [ -e "$ALIAS_BASE" ] && [ ! -L "$ALIAS_BASE" ] && [ ! -d "$ALIAS_BASE" ]; then
  echo "ERROR: alias path exists but is not a dir/symlink: $ALIAS_BASE" >&2
  exit 2
fi

if [ ! -e "$ALIAS_BASE" ]; then
  if ! ln -s "$TARGET_BASE" "$ALIAS_BASE" 2>/dev/null; then
    # If the chosen alias location is not writable for some reason, fall back to /tmp.
    if [ "$ALIAS_BASE" != "/tmp/lsnap" ]; then
      ALIAS_BASE="/tmp/lsnap"
      if [ ! -e "$ALIAS_BASE" ]; then
        ln -s "$TARGET_BASE" "$ALIAS_BASE"
      fi
    else
      echo "ERROR: failed to create alias symlink at $ALIAS_BASE" >&2
      exit 2
    fi
  fi
fi

if [ -L "$ALIAS_BASE" ]; then
  alias_target="$(readlink -f "$ALIAS_BASE" || true)"
  real_target="$(readlink -f "$TARGET_BASE" || true)"
  if [ -n "$alias_target" ] && [ -n "$real_target" ] && [ "$alias_target" != "$real_target" ]; then
    echo "ERROR: $ALIAS_BASE points to $alias_target, expected $real_target" >&2
    echo "Fix the symlink or set LEAN_SNAPSHOT_ALIAS_BASE to a different path." >&2
    exit 2
  fi
fi

SNAP_DIR="${SNAP_DIR:-$ALIAS_BASE/fc_lean_snap_ready-$TS}"
export SNAP_DIR

export EAGER_MATHLIB_IMPORT="${EAGER_MATHLIB_IMPORT:-1}"
export REQUIRE_WARMUP="${REQUIRE_WARMUP:-1}"
export REPL_WARM_RETRIES="${REPL_WARM_RETRIES:-1}"

# Timeouts: keep bounded so failures are decisive.
export MATHLIB_IMPORT_TIMEOUT_S="${MATHLIB_IMPORT_TIMEOUT_S:-1800}"
export WARMUP_PROBE_TIMEOUT_S="${WARMUP_PROBE_TIMEOUT_S:-60}"
export SNAP_WAIT_TIMEOUT_S="${SNAP_WAIT_TIMEOUT_S:-3600}"

# Pickle/unpickle warm-cache behavior
export USE_MATHLIB_PICKLE="${USE_MATHLIB_PICKLE:-1}"
export PICKLE_AFTER_IMPORT="${PICKLE_AFTER_IMPORT:-1}"
export MATHLIB_PICKLE_PATH="${MATHLIB_PICKLE_PATH:-/mnt/data/mathlib_env.olean}"

# Logging verbosity
export SERIAL_POLL_INTERVAL_S="${SERIAL_POLL_INTERVAL_S:-10}"
export SERIAL_HEARTBEAT_S="${SERIAL_HEARTBEAT_S:-60}"
export FAILURE_SERIAL_TAIL_LINES="${FAILURE_SERIAL_TAIL_LINES:-8000}"

# Do not `mkdir -p $ALIAS_BASE` here: the alias is typically a symlink, and `mkdir`
# will error even if it points at an existing directory. `fc_create_lean_snapshot_vsock.sh`
# will create `$SNAP_DIR` as needed (and the path will resolve through the symlink).
echo "SNAP_DIR=$SNAP_DIR"
echo "Running snapshot build (will prompt for sudo if needed)..."
exec sudo -E ./fc_create_lean_snapshot_vsock.sh
