#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ARTIFACT_DIR="${P16_ARTIFACT_DIR:-$ROOT_DIR/../docs_tmp/cli_phase_6/CODESIGN_p16/implementation_validation_p16_final_design_complete/artifacts/multiagent/phase_g_recovery_tasks_$(date +%Y%m%d-%H%M%S)}"
rm -rf "$ARTIFACT_DIR"
mkdir -p "$ARTIFACT_DIR/dummy_workspace/.breadboard/subagents" "$ARTIFACT_DIR/home" "$ARTIFACT_DIR/scripts"

STATE_PATH="$ARTIFACT_DIR/state.ndjson"
SNAPSHOT_PATH="$ARTIFACT_DIR/snapshots.txt"
HARNESS_OUT="$ARTIFACT_DIR/harness_output.txt"
KILL_LOG="$ARTIFACT_DIR/kill.log"
SCRIPT_PATH="$ARTIFACT_DIR/scripts/multiagent_recovery_tasks.json"
ENGINE_PORT="${P16_MULTIAGENT_RECOVERY_ENGINE_PORT:-19287}"
ENGINE_URL="http://127.0.0.1:${ENGINE_PORT}"
PROMPT="Run the deterministic P16 multiagent recovery task-state scenario."
MOCK_SCRIPT="scripts/mock_sse_multiagent_recovery_slow.json"
ENGINE_BIN="node"
ENGINE_ARGS="--import tsx tools/mock/mockSseServer.ts --script ${MOCK_SCRIPT} --host 127.0.0.1 --port ${ENGINE_PORT}"

cat > "$ARTIFACT_DIR/prompt.txt" <<<"$PROMPT"
cat > "$ARTIFACT_DIR/dummy_workspace/.breadboard/subagents/recovery-implementer-01.jsonl" <<'JSONL'
{"event":"start","message":"RECOVERY_TASK_TAIL_LINE_01 implementer started"}
{"event":"write","path":"recovery_state.c","message":"RECOVERY_TASK_TAIL_LINE_02 task state preserved"}
{"event":"summary","message":"RECOVERY_TASK_TAIL_LINE_03 deterministic recovery marker"}
JSONL
cat > "$SCRIPT_PATH" <<JSON
[
  { "action": "waitForComposerReady", "timeoutMs": 45000 },
  { "action": "type", "text": "$PROMPT", "typingDelayMs": 4 },
  { "action": "press", "key": "enter" },
  { "action": "waitForState", "timeoutMs": 45000, "pendingResponse": true, "lastLiveSlotStatus": "pending", "lastLiveSlotTextIncludes": "TASK_RECOVERY_STDOUT_A", "lifecycleMode": "local-owned", "lifecycleOwned": true, "lifecyclePidPresent": true },
  { "action": "snapshot", "label": "multiagent-recovery-before-kill", "maxLines": 140, "mode": "frame" },
  { "action": "waitForState", "timeoutMs": 45000, "disconnected": true, "statusIncludes": "Recovery needed", "lifecycleMode": "local-owned", "lifecycleOwned": true, "lifecyclePidPresent": true },
  { "action": "waitFor", "text": "state=preserved-local", "timeoutMs": 10000, "mode": "either" },
  { "action": "waitFor", "text": "tasks=3 running=1 blocked=1 failed=0 completed=1", "timeoutMs": 10000, "mode": "either" },
  { "action": "snapshot", "label": "multiagent-recovery-copy", "maxLines": 160, "mode": "frame" },
  { "action": "press", "key": "ctrl+b" },
  { "action": "waitFor", "text": "Background tasks", "timeoutMs": 5000, "mode": "frame" },
  { "action": "waitFor", "text": "Implement recovery task state", "timeoutMs": 5000, "mode": "frame" },
  { "action": "waitFor", "text": "Test recovery task state", "timeoutMs": 5000, "mode": "frame" },
  { "action": "snapshot", "label": "multiagent-recovery-taskboard", "maxLines": 160, "mode": "frame" },
  { "action": "resize", "cols": 86, "rows": 24, "delayMs": 350 },
  { "action": "waitFor", "text": "Background tasks", "timeoutMs": 5000, "mode": "frame" },
  { "action": "waitFor", "text": "recovery-implementer-01", "timeoutMs": 5000, "mode": "frame" },
  { "action": "snapshot", "label": "multiagent-recovery-taskboard-compact", "maxLines": 160, "mode": "frame" },
  { "action": "press", "key": "escape" },
  { "action": "wait", "ms": 250 },
  { "action": "snapshot", "label": "multiagent-recovery-closed", "maxLines": 160, "mode": "frame" }
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
      const liveSlots = Array.isArray(state?.liveSlots) ? state.liveSlots : []
      const tasks = Array.isArray(state?.tasks) ? state.tasks : []
      const hasStdout = liveSlots.some((entry) => String(entry?.text ?? '').includes('TASK_RECOVERY_STDOUT_A'))
      const hasTasks = tasks.some((entry) => String(entry?.id ?? '') === 'recovery-implementer-01') && tasks.length >= 3
      if (state?.pendingResponse === true && hasStdout && hasTasks && lifecycle?.mode === 'local-owned' && lifecycle?.owned === true && Number.isFinite(pid)) {
        await sleep(1000)
        process.kill(pid, 'SIGKILL')
        await fs.writeFile(killLog, `killed_pid=${pid}\nmode=${lifecycle.mode}\nstatus=${state?.status ?? ''}\ntasks=${tasks.length}\ntimestamp=${record?.timestamp ?? ''}\n`, 'utf8')
        killed = true
        break
      }
    }
  } catch {}
  if (!killed) await sleep(50)
}
if (!killed) {
  await fs.writeFile(killLog, 'failed_to_find_task_stdout_lifecycle_pid=1\n', 'utf8')
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
BREADBOARD_SUBAGENTS_V2_ENABLED=1 \
BREADBOARD_SUBAGENTS_STRIP_ENABLED=1 \
BREADBOARD_SUBAGENTS_TOASTS_ENABLED=1 \
BREADBOARD_SUBAGENTS_TASKBOARD_ENABLED=1 \
BREADBOARD_SUBAGENTS_FOCUS_ENABLED=1 \
BREADBOARD_SUBAGENTS_COALESCE_MS=0 \
BREADBOARD_TUI_SUBAGENTS_ENABLED=1 \
BREADBOARD_TUI_SUBAGENTS_STRIP_ENABLED=1 \
BREADBOARD_TUI_SUBAGENTS_TOASTS_ENABLED=1 \
BREADBOARD_TUI_SUBAGENTS_TASKBOARD_ENABLED=1 \
BREADBOARD_TUI_SUBAGENTS_FOCUS_ENABLED=1 \
BREADBOARD_TUI_SUBAGENTS_COALESCE_MS=0 \
bash scripts/run_legacy_pty_case.sh \
  --script "$SCRIPT_PATH" \
  --snapshots "$SNAPSHOT_PATH" \
  --cmd "node dist/main.js repl --tui classic --workspace $ARTIFACT_DIR/dummy_workspace" \
  --cols 132 \
  --rows 36 \
  --watchdog-ms 45000 \
  --submit-timeout-ms 0 \
  --max-duration-ms 150000 \
  > "$HARNESS_OUT" 2>&1
HARNESS_STATUS=$?
set -e

wait "$KILLER_PID" || true

node --import tsx scripts/qc_p16_multiagent_recovery_tasks_gate.ts "$ARTIFACT_DIR" "$HARNESS_STATUS"
GATE_STATUS=$?

cat <<REPORT
[p16][multiagent-recovery-tasks] artifact_dir=$ARTIFACT_DIR
[p16][multiagent-recovery-tasks] harness_status=$HARNESS_STATUS
[p16][multiagent-recovery-tasks] gate_status=$GATE_STATUS
REPORT

exit "$GATE_STATUS"
