#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ARTIFACT_DIR="${V7_ARTIFACT_DIR:-$ROOT_DIR/scripts/_tmp_v7_doctor_footer}"
rm -rf "$ARTIFACT_DIR"
mkdir -p "$ARTIFACT_DIR/dummy_workspace" "$ARTIFACT_DIR/home" "$ARTIFACT_DIR/scripts"

STATE_PATH="$ARTIFACT_DIR/state.ndjson"
SNAPSHOT_PATH="$ARTIFACT_DIR/snapshots.txt"
HARNESS_OUT="$ARTIFACT_DIR/harness_output.txt"
SCRIPT_PATH="$ARTIFACT_DIR/scripts/doctor_footer.json"
ENGINE_PORT="${V7_DOCTOR_FOOTER_PORT:-19179}"
ENGINE_URL="http://127.0.0.1:${ENGINE_PORT}"

cat > "$SCRIPT_PATH" <<'JSON'
[
  { "action": "waitForComposerReady", "timeoutMs": 45000, "mode": "either" },
  { "action": "type", "text": "/doctor" },
  { "action": "press", "key": "enter" },
  { "action": "waitForState", "timeoutMs": 45000, "lastToolEventKind": "status", "lastToolEventTextIncludes": "[doctor]" },
  { "action": "snapshot", "label": "doctor-report", "maxLines": 140, "mode": "frame" }
]
JSON

set +e
BREADBOARD_STATE_DUMP_PATH="$STATE_PATH" \
BREADBOARD_STATE_DUMP_MODE=full \
BREADBOARD_STATE_DUMP_RATE_MS=50 \
BREADBOARD_PTY_HOME="$ARTIFACT_DIR/home" \
BREADBOARD_ENGINE_MODE=local-owned \
BREADBOARD_API_URL="$ENGINE_URL" \
BREADBOARD_ENGINE_KEEPALIVE=0 \
bash scripts/run_legacy_pty_case.sh \
  --script "$SCRIPT_PATH" \
  --snapshots "$SNAPSHOT_PATH" \
  --cmd "node dist/main.js repl --tui classic --workspace $ARTIFACT_DIR/dummy_workspace" \
  --cols 120 \
  --rows 36 \
  --watchdog-ms 30000 \
  --max-duration-ms 90000 \
  > "$HARNESS_OUT" 2>&1
HARNESS_STATUS=$?
set -e

node --import tsx scripts/qc_v7_doctor_footer_gate.ts "$ARTIFACT_DIR" "$HARNESS_STATUS"
GATE_STATUS=$?

cat <<REPORT
[v7][doctor-footer] artifact_dir=$ARTIFACT_DIR
[v7][doctor-footer] harness_status=$HARNESS_STATUS
[v7][doctor-footer] gate_status=$GATE_STATUS
REPORT

exit "$GATE_STATUS"
