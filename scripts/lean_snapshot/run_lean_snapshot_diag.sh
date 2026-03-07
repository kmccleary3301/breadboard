#!/usr/bin/env bash
set -euo pipefail

# Diagnostic runner for Lean/Mathlib warm snapshot creation.
#
# Usage:
#   1) Ensure sudo is unlocked in this shell: `sudo -v`
#   2) Run: `./run_lean_snapshot_diag.sh`
#
# Notes:
# - This does NOT delete anything. It writes a new SNAP_DIR under /tmp.
# - It enables eager warmup + pickling to /mnt/data/mathlib_env.olean inside the guest.
#   That file will live inside the run's `data.ext4` image and can be reused via unpickle
#   on subsequent boots.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SNAP_DIR="${SNAP_DIR:-/tmp/fc_lean_snap_diag-$(date +%Y%m%d-%H%M%S)}"
export SNAP_DIR

export EAGER_MATHLIB_IMPORT="${EAGER_MATHLIB_IMPORT:-1}"
export REQUIRE_WARMUP="${REQUIRE_WARMUP:-1}"
export REPL_WARM_RETRIES="${REPL_WARM_RETRIES:-1}"

# Timeouts: keep these bounded so failures are decisive.
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

echo "SNAP_DIR=$SNAP_DIR"
echo "Running snapshot build (will prompt for sudo if needed)..."
exec sudo -E "$SCRIPT_DIR/fc_create_lean_snapshot_vsock.sh"
