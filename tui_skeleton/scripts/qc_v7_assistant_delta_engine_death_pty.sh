#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ARTIFACT_DIR="${V7_ARTIFACT_DIR:-$ROOT_DIR/scripts/_tmp_v7_assistant_delta_engine_death}"
rm -rf "$ARTIFACT_DIR"
mkdir -p "$ARTIFACT_DIR/dummy_workspace" "$ARTIFACT_DIR/home" "$ARTIFACT_DIR/scripts"

STATE_PATH="$ARTIFACT_DIR/state.ndjson"
SNAPSHOT_PATH="$ARTIFACT_DIR/snapshots.txt"
HARNESS_OUT="$ARTIFACT_DIR/harness_output.txt"
KILL_LOG="$ARTIFACT_DIR/kill.log"
SCRIPT_PATH="$ARTIFACT_DIR/scripts/assistant_delta_engine_death.json"
ENGINE_PORT="${V7_ASSISTANT_DELTA_ENGINE_PORT:-19129}"
ENGINE_URL="http://127.0.0.1:${ENGINE_PORT}"
PROMPT="Run the deterministic V7 assistant delta scenario."
MOCK_SCRIPT="scripts/mock_sse_v7_assistant_delta_slow.json"
ENGINE_BIN="node"
ENGINE_ARGS="--import tsx tools/mock/mockSseServer.ts --script ${MOCK_SCRIPT} --host 127.0.0.1 --port ${ENGINE_PORT}"

cat > "$ARTIFACT_DIR/prompt.txt" <<<"$PROMPT"
cat > "$SCRIPT_PATH" <<JSON
[
  { "action": "waitForComposerReady", "timeoutMs": 45000, "mode": "either" },
  { "action": "type", "text": "$PROMPT" },
  { "action": "press", "key": "enter" },
  { "action": "waitForState", "timeoutMs": 45000, "pendingResponse": true, "lastConversationSpeaker": "assistant", "lastConversationPreviewIncludes": "V7-ASSISTANT-DELTA-A", "lifecycleMode": "local-owned", "lifecycleOwned": true, "lifecyclePidPresent": true },
  { "action": "snapshot", "label": "after-assistant-delta-before-kill", "maxLines": 120, "mode": "frame" },
  { "action": "wait", "ms": 6500 },
  { "action": "snapshot", "label": "after-assistant-delta-kill", "maxLines": 120, "mode": "frame" }
]
JSON

(
  node --input-type=module - "$STATE_PATH" "$KILL_LOG" <<'NODE'
import fs from 'node:fs/promises'
import process from 'node:process'
const [statePath, killLog] = process.argv.slice(2)
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const deadline = Date.now() + 60000
let killed = false
while (Date.now() < deadline && !killed) {
  try {
    const raw = await fs.readFile(statePath, 'utf8')
    const lines = raw.trim().split(/\r?\n/).filter(Boolean)
    for (const line of lines.reverse()) {
      const record = JSON.parse(line)
      const state = record?.state
      const lifecycle = state?.lifecycle
      const pid = lifecycle?.pid
      const conversation = Array.isArray(state?.conversation) ? state.conversation : []
      const hasDelta = conversation.some((entry) => entry?.speaker === 'assistant' && String(entry?.text ?? '').includes('V7-ASSISTANT-DELTA-A'))
      if (state?.pendingResponse === true && hasDelta && lifecycle?.mode === 'local-owned' && lifecycle?.owned === true && Number.isFinite(pid)) {
        process.kill(pid, 'SIGKILL')
        await fs.writeFile(killLog, `killed_pid=${pid}\nmode=${lifecycle.mode}\nstatus=${state?.status ?? ''}\ntimestamp=${record?.timestamp ?? ''}\n`, 'utf8')
        killed = true
        break
      }
    }
  } catch {}
  if (!killed) await sleep(50)
}
if (!killed) {
  await fs.writeFile(killLog, 'failed_to_find_assistant_delta_lifecycle_pid=1\n', 'utf8')
  process.exit(1)
}
NODE
) &
KILLER_PID=$!

set +e
BREADBOARD_STATE_DUMP_PATH="$STATE_PATH" \
BREADBOARD_STATE_DUMP_MODE=full \
BREADBOARD_STATE_DUMP_RATE_MS=50 \
BREADBOARD_PTY_HOME="$ARTIFACT_DIR/home" \
BREADBOARD_ENGINE_MODE=local-owned \
BREADBOARD_API_URL="$ENGINE_URL" \
BREADBOARD_ENGINE_BIN="$ENGINE_BIN" \
BREADBOARD_ENGINE_ARGS="$ENGINE_ARGS" \
BREADBOARD_ENGINE_KEEPALIVE=0 \
BREADBOARD_MOCK_CLOSE_ON_FINISH=0 \
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

wait "$KILLER_PID" || true

node --import tsx scripts/qc_v7_assistant_delta_engine_death_gate.ts "$ARTIFACT_DIR"
GATE_STATUS=$?

cat <<REPORT
[v7][assistant-delta-engine-death] artifact_dir=$ARTIFACT_DIR
[v7][assistant-delta-engine-death] harness_status=$HARNESS_STATUS
[v7][assistant-delta-engine-death] gate_status=$GATE_STATUS
REPORT

exit "$GATE_STATUS"
