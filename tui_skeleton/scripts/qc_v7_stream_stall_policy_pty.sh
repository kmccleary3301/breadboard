#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ARTIFACT_DIR="${V7_ARTIFACT_DIR:-$ROOT_DIR/scripts/_tmp_v7_stream_stall_policy}"
rm -rf "$ARTIFACT_DIR"
mkdir -p "$ARTIFACT_DIR/dummy_workspace" "$ARTIFACT_DIR/home" "$ARTIFACT_DIR/scripts"

STATE_PATH="$ARTIFACT_DIR/state.ndjson"
SNAPSHOT_PATH="$ARTIFACT_DIR/snapshots.txt"
HARNESS_OUT="$ARTIFACT_DIR/harness_output.txt"
SCRIPT_PATH="$ARTIFACT_DIR/scripts/stream_stall_policy.json"
ENGINE_PORT="${V7_STREAM_STALL_ENGINE_PORT:-19240}"
ENGINE_URL="http://127.0.0.1:${ENGINE_PORT}"
PROMPT="Trigger V7 stream stall policy."

cat > "$ARTIFACT_DIR/prompt.txt" <<<"$PROMPT"
cat > "$SCRIPT_PATH" <<JSON
[
  { "action": "waitForComposerReady", "timeoutMs": 45000, "mode": "either" },
  { "action": "type", "text": "$PROMPT" },
  { "action": "press", "key": "enter" },
  { "action": "waitForState", "timeoutMs": 45000, "pendingResponse": true, "conversationCountAtLeast": 1 },
  { "action": "waitForState", "timeoutMs": 45000, "disconnected": true, "statusIncludes": "Disconnected" },
  { "action": "snapshot", "label": "stream-stall-disconnected", "maxLines": 120, "mode": "frame" }
]
JSON

set +e
BREADBOARD_STATE_DUMP_PATH="$STATE_PATH" \
BREADBOARD_STATE_DUMP_MODE=summary \
BREADBOARD_STATE_DUMP_RATE_MS=50 \
BREADBOARD_PTY_HOME="$ARTIFACT_DIR/home" \
BREADBOARD_ENGINE_MODE=local-owned \
BREADBOARD_API_URL="$ENGINE_URL" \
BREADBOARD_ENGINE_BIN=node \
BREADBOARD_ENGINE_ARGS="--import tsx tools/mock/mockSseServer.ts --script scripts/mock_sse_v7_stream_stall_hold.json --host 127.0.0.1 --port $ENGINE_PORT" \
BREADBOARD_ENGINE_KEEPALIVE=0 \
BREADBOARD_STREAM_MAX_RETRIES=2 \
BREADBOARD_TUI_STREAM_STALL_TIMEOUT_MS=500 \
bash scripts/run_legacy_pty_case.sh \
  --script "$SCRIPT_PATH" \
  --snapshots "$SNAPSHOT_PATH" \
  --cmd "node dist/main.js repl --tui classic --workspace $ARTIFACT_DIR/dummy_workspace" \
  --cols 120 \
  --rows 36 \
  --watchdog-ms 20000 \
  --max-duration-ms 90000 \
  > "$HARNESS_OUT" 2>&1
HARNESS_STATUS=$?
set -e

node --import tsx scripts/qc_v7_stream_stall_policy_gate.ts "$ARTIFACT_DIR" "$HARNESS_STATUS"
GATE_STATUS=$?

cat <<REPORT
[v7][stream-stall-policy] artifact_dir=$ARTIFACT_DIR
[v7][stream-stall-policy] harness_status=$HARNESS_STATUS
[v7][stream-stall-policy] gate_status=$GATE_STATUS
REPORT

exit "$GATE_STATUS"
