#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ARTIFACT_DIR="${V7_ARTIFACT_DIR:-$ROOT_DIR/scripts/_tmp_v7_resume_after_recovery}"
rm -rf "$ARTIFACT_DIR"
mkdir -p "$ARTIFACT_DIR/dummy_workspace" "$ARTIFACT_DIR/home" "$ARTIFACT_DIR/scripts"

STATE_PATH="$ARTIFACT_DIR/state.ndjson"
SNAPSHOT_PATH="$ARTIFACT_DIR/snapshots.txt"
HARNESS_OUT="$ARTIFACT_DIR/harness_output.txt"
KILL_LOG="$ARTIFACT_DIR/kill.log"
SCRIPT_PATH="$ARTIFACT_DIR/scripts/resume_after_recovery.json"
ENGINE_PORT="${V7_RESUME_AFTER_RECOVERY_PORT:-19221}"
ENGINE_URL="http://127.0.0.1:${ENGINE_PORT}"
PROMPT="Trigger V7 resume after recovery."
MOCK_SCRIPT="scripts/mock_sse_v7_retry_after_unknown.json"
ENGINE_BIN="node"
ENGINE_ARGS="--import tsx tools/mock/mockSseServer.ts --script ${MOCK_SCRIPT} --host 127.0.0.1 --port ${ENGINE_PORT}"

cat > "$ARTIFACT_DIR/prompt.txt" <<<"$PROMPT"
cat > "$SCRIPT_PATH" <<JSON
[
  { "action": "waitForComposerReady", "timeoutMs": 45000, "mode": "either" },
  { "action": "type", "text": "$PROMPT" },
  { "action": "press", "key": "enter" },
  { "action": "waitForState", "timeoutMs": 45000, "pendingResponse": true, "conversationCountAtLeast": 1, "lifecycleMode": "local-owned", "lifecycleOwned": true, "lifecyclePidPresent": true },
  { "action": "waitForState", "timeoutMs": 45000, "disconnected": true, "statusIncludes": "Recovery needed" },
  { "action": "type", "text": "/resume" },
  { "action": "press", "key": "enter" },
  { "action": "waitForState", "timeoutMs": 45000, "disconnected": false, "pendingResponse": false, "statusIncludes": "Recovered", "lastToolEventKind": "status", "lastToolEventTextIncludes": "resume=recovered-new-session" },
  { "action": "snapshot", "label": "resume-after-recovery-final", "maxLines": 160, "mode": "frame" }
]
JSON

(
  node --input-type=module - "$STATE_PATH" "$KILL_LOG" "$PROMPT" <<'NODE'
import fs from 'node:fs/promises'
import process from 'node:process'
const [statePath, killLog, prompt] = process.argv.slice(2)
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
      const hasPrompt = conversation.some((entry) => entry?.speaker === 'user' && String(entry?.text ?? '').includes(prompt.slice(0, 24)))
      if (state?.pendingResponse === true && hasPrompt && lifecycle?.mode === 'local-owned' && lifecycle?.owned === true && Number.isFinite(pid)) {
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
  await fs.writeFile(killLog, 'failed_to_find_pending_user_echo_lifecycle_pid=1\n', 'utf8')
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
BREADBOARD_MOCK_CLOSE_ON_FINISH=1 \
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

node --import tsx scripts/qc_v7_resume_after_recovery_gate.ts "$ARTIFACT_DIR" "$HARNESS_STATUS"
GATE_STATUS=$?

cat <<REPORT
[v7][resume-after-recovery] artifact_dir=$ARTIFACT_DIR
[v7][resume-after-recovery] harness_status=$HARNESS_STATUS
[v7][resume-after-recovery] gate_status=$GATE_STATUS
REPORT

exit "$GATE_STATUS"
