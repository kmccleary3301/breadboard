#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ARTIFACT_DIR="${PROVIDER_AUTH_DEDUPE_ARTIFACT_DIR:-$ROOT_DIR/scripts/_tmp_provider_auth_resize_dedupe}"
rm -rf "$ARTIFACT_DIR"
mkdir -p "$ARTIFACT_DIR/dummy_workspace" "$ARTIFACT_DIR/home" "$ARTIFACT_DIR/scripts"

STATE_PATH="$ARTIFACT_DIR/state.ndjson"
SNAPSHOT_PATH="$ARTIFACT_DIR/snapshots.txt"
HARNESS_OUT="$ARTIFACT_DIR/harness_output.txt"
SCRIPT_PATH="$ARTIFACT_DIR/scripts/provider_auth_resize_dedupe.json"
MOCK_PORT="${PROVIDER_AUTH_DEDUPE_PORT:-19391}"
MOCK_URL="http://127.0.0.1:${MOCK_PORT}"
MOCK_SCRIPT="scripts/mock_sse_provider_auth_dedupe.json"
MOCK_LOG="$ARTIFACT_DIR/mock.log"

cleanup() {
  if [[ -n "${MOCK_PID:-}" ]]; then
    kill "$MOCK_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

cat > "$SCRIPT_PATH" <<JSON
[
  { "action": "waitForComposerReady", "timeoutMs": 45000, "mode": "either" },
  { "action": "type", "text": "Trigger provider auth dedupe." },
  { "action": "press", "key": "enter" },
  { "action": "waitFor", "text": "api.responses.write", "timeoutMs": 45000, "mode": "either" },
  { "action": "snapshot", "label": "provider-auth-initial", "maxLines": 140, "mode": "frame" },
  { "action": "resize", "cols": 90, "rows": 26, "delayMs": 300 },
  { "action": "snapshot", "label": "provider-auth-narrow", "maxLines": 140, "mode": "frame" },
  { "action": "resize", "cols": 132, "rows": 36, "delayMs": 300 },
  { "action": "snapshot", "label": "provider-auth-wide", "maxLines": 140, "mode": "frame" },
  { "action": "resize", "cols": 78, "rows": 24, "delayMs": 300 },
  { "action": "snapshot", "label": "provider-auth-final-narrow", "maxLines": 140, "mode": "frame" }
]
JSON

node --import tsx tools/mock/mockSseServer.ts \
  --script "$MOCK_SCRIPT" \
  --host 127.0.0.1 \
  --port "$MOCK_PORT" \
  > "$MOCK_LOG" 2>&1 &
MOCK_PID=$!
sleep 1

set +e
BREADBOARD_STATE_DUMP_PATH="$STATE_PATH" \
BREADBOARD_STATE_DUMP_MODE=full \
BREADBOARD_STATE_DUMP_RATE_MS=50 \
BREADBOARD_PTY_HOME="$ARTIFACT_DIR/home" \
BREADBOARD_ENGINE_MODE=external \
BREADBOARD_API_URL="$MOCK_URL" \
bash scripts/run_legacy_pty_case.sh \
  --script "$SCRIPT_PATH" \
  --snapshots "$SNAPSHOT_PATH" \
  --cmd "node dist/main.js repl --tui classic --workspace $ARTIFACT_DIR/dummy_workspace" \
  --cols 120 \
  --rows 36 \
  --watchdog-ms 30000 \
  --submit-timeout-ms 0 \
  --max-duration-ms 120000 \
  > "$HARNESS_OUT" 2>&1
HARNESS_STATUS=$?
set -e

node --import tsx scripts/qc_provider_auth_resize_dedupe_gate.ts "$ARTIFACT_DIR" "$HARNESS_STATUS"
GATE_STATUS=$?

cat <<REPORT
[provider-auth-resize] artifact_dir=$ARTIFACT_DIR
[provider-auth-resize] harness_status=$HARNESS_STATUS
[provider-auth-resize] gate_status=$GATE_STATUS
REPORT

exit "$GATE_STATUS"
