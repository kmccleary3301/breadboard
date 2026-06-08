#!/usr/bin/env bash
set -euo pipefail

LANE="${1:-ghostty}"
if [ "$LANE" != "ghostty" ] && [ "$LANE" != "wezterm" ]; then
  echo "Usage: $0 [ghostty|wezterm]" >&2
  exit 2
fi

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd -- "$ROOT_DIR/.." && pwd)"
cd "$ROOT_DIR"

ARTIFACT_ROOT="$REPO_ROOT/docs_tmp/cli_phase_6/CODESIGN_p14/implementation_validation_v7_engine_lifecycle/V7-G-real-terminal-gauntlet"
RUN_NAME="${LANE}_resume_after_recovery_run_1"
if [ -n "${V7_ARTIFACT_DIR:-}" ]; then
  ARTIFACT_DIR="$V7_ARTIFACT_DIR"
  FINAL_GREEN_DIR="$V7_ARTIFACT_DIR"
  FINAL_RED_DIR="$V7_ARTIFACT_DIR"
else
  ARTIFACT_DIR="$ARTIFACT_ROOT/work/$RUN_NAME"
  FINAL_GREEN_DIR="$ARTIFACT_ROOT/green/$RUN_NAME"
  FINAL_RED_DIR="$ARTIFACT_ROOT/red/$RUN_NAME"
fi
rm -rf "$ARTIFACT_DIR"
mkdir -p "$ARTIFACT_DIR/dummy_workspace" "$ARTIFACT_DIR/home" "$ARTIFACT_DIR/scripts" "$ARTIFACT_DIR/runtime"

STATE_PATH="$ARTIFACT_DIR/state.ndjson"
SNAPSHOT_PATH="$ARTIFACT_DIR/snapshots.txt"
METADATA_PATH="$ARTIFACT_DIR/metadata.json"
FRAMES_PATH="$ARTIFACT_DIR/frames.json"
INPUT_LOG_PATH="$ARTIFACT_DIR/input_log.ndjson"
VISIBLE_TEXT_PATH="$ARTIFACT_DIR/visible_text_final.txt"
SCROLLBACK_TEXT_PATH="$ARTIFACT_DIR/scrollback_text_final.txt"
HARNESS_OUT="$ARTIFACT_DIR/harness_output.txt"
KILL_LOG="$ARTIFACT_DIR/kill.log"
SCRIPT_PATH="$ARTIFACT_DIR/scripts/real_terminal_resume_after_recovery.json"
WRAPPER_PATH="$ARTIFACT_DIR/scripts/run_repl_with_engine_killer.sh"
PROMPT="Trigger V7 ${LANE} real terminal resume recovery."
PORT_OFFSET=0
if [ "$LANE" = "wezterm" ]; then PORT_OFFSET=1; fi
ENGINE_PORT="${V7_REAL_TERMINAL_ENGINE_PORT:-$((19230 + PORT_OFFSET))}"
ENGINE_URL="http://127.0.0.1:${ENGINE_PORT}"
MOCK_SCRIPT="scripts/mock_sse_v7_real_terminal_hold.json"

cat > "$ARTIFACT_DIR/prompt.txt" <<<"$PROMPT"
if [ "$LANE" = "ghostty" ]; then
cat > "$SCRIPT_PATH" <<JSON
[
  { "action": "wait", "ms": 14000 },
  { "action": "snapshot", "label": "${LANE}-startup" },
  { "action": "type", "text": "$PROMPT" },
  { "action": "press", "key": "enter" },
  { "action": "wait", "ms": 6500 },
  { "action": "snapshot", "label": "${LANE}-recovery-needed" },
  { "action": "resize", "cols": 92, "rows": 28, "delayMs": 1500 },
  { "action": "snapshot", "label": "${LANE}-recovery-resized" },
  { "action": "paste", "text": "/resume" },
  { "action": "press", "key": "enter" },
  { "action": "wait", "ms": 4500 },
  { "action": "snapshot", "label": "${LANE}-resume-recovered" }
]
JSON
else
cat > "$SCRIPT_PATH" <<JSON
[
  { "action": "waitForComposerReady", "timeoutMs": 60000 },
  { "action": "type", "text": "$PROMPT" },
  { "action": "press", "key": "enter" },
  { "action": "waitForState", "timeoutMs": 60000, "pendingResponse": true, "conversationCountAtLeast": 1, "lifecycleMode": "local-owned", "lifecycleOwned": true, "lifecyclePidPresent": true },
  { "action": "waitForState", "timeoutMs": 60000, "disconnected": true, "statusIncludes": "Recovery needed" },
  { "action": "snapshot", "label": "${LANE}-recovery-needed" },
  { "action": "resize", "cols": 92, "rows": 28, "delayMs": 6000 },
  { "action": "waitForState", "timeoutMs": 60000, "disconnected": true, "statusIncludes": "Recovery needed" },
  { "action": "snapshot", "label": "${LANE}-recovery-resized" },
  { "action": "type", "text": "/resume" },
  { "action": "press", "key": "enter" },
  { "action": "waitForState", "timeoutMs": 60000, "disconnected": false, "pendingResponse": false, "statusIncludes": "Recovered", "lastToolEventKind": "status", "lastToolEventTextIncludes": "resume=recovered-new-session" },
  { "action": "snapshot", "label": "${LANE}-resume-recovered" }
]
JSON
fi

cat > "$WRAPPER_PATH" <<'EOF_WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
: "${BREADBOARD_STATE_DUMP_PATH:?BREADBOARD_STATE_DUMP_PATH required}"
: "${V7_KILL_LOG:?V7_KILL_LOG required}"
: "${V7_PROMPT:?V7_PROMPT required}"
(
  node --input-type=module - "$BREADBOARD_STATE_DUMP_PATH" "$V7_KILL_LOG" "$V7_PROMPT" <<'NODE'
import fs from 'node:fs/promises'
import process from 'node:process'
const [statePath, killLog, prompt] = process.argv.slice(2)
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const deadline = Date.now() + 90000
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
      const transcriptCells = Array.isArray(state?.transcriptCells) ? state.transcriptCells : []
      const hasPrompt =
        String(state?.lastConversation?.preview ?? '').includes(prompt.slice(0, 28)) ||
        transcriptCells.some((entry) =>
          (entry?.speaker === 'user' || entry?.role === 'user-request') &&
          String(entry?.textPreview ?? entry?.text ?? '').includes(prompt.slice(0, 28))
        )
      if (state?.pendingResponse === true && hasPrompt && lifecycle?.mode === 'local-owned' && lifecycle?.owned === true && Number.isFinite(pid)) {
        try {
          process.kill(pid, 'SIGKILL')
          await fs.writeFile(killLog, `killed_pid=${pid}\nmode=${lifecycle.mode}\nstatus=${state?.status ?? ''}\ntimestamp=${record?.timestamp ?? ''}\n`, 'utf8')
        } catch (error) {
          await fs.writeFile(killLog, `kill_error=${error instanceof Error ? error.message : String(error)}\npid=${pid}\n`, 'utf8')
        }
        killed = true
        break
      }
    }
  } catch {}
  if (!killed) await sleep(50)
}
if (!killed) {
  await fs.writeFile(killLog, 'failed_to_find_pending_user_echo_lifecycle_pid=1\n', 'utf8')
}
NODE
) &
KILLER_PID=$!
node dist/main.js repl --tui classic --workspace "${V7_WORKSPACE:-$PWD}"
STATUS=$?
wait "$KILLER_PID" || true
exit "$STATUS"
EOF_WRAPPER
chmod +x "$WRAPPER_PATH"

set +e
timeout 180s node --import tsx scripts/harness/terminalAdapterCli.ts \
  --lane "$LANE" \
  --script "$SCRIPT_PATH" \
  --cmd "bash $WRAPPER_PATH" \
  --cwd "$ROOT_DIR" \
  --runtime-dir "$ARTIFACT_DIR/runtime" \
  --snapshots "$SNAPSHOT_PATH" \
  --metadata "$METADATA_PATH" \
  --frames "$FRAMES_PATH" \
  --input-log "$INPUT_LOG_PATH" \
  --visible-text "$VISIBLE_TEXT_PATH" \
  --scrollback-text "$SCROLLBACK_TEXT_PATH" \
  --state-dump "$STATE_PATH" \
  --cols 120 \
  --rows 36 \
  --env "BREADBOARD_PTY_HOME=$ARTIFACT_DIR/home" \
  --env "BREADBOARD_ENGINE_MODE=local-owned" \
  --env "BREADBOARD_API_URL=$ENGINE_URL" \
  --env "BREADBOARD_ENGINE_BIN=node" \
  --env "BREADBOARD_ENGINE_ARGS=--import tsx tools/mock/mockSseServer.ts --script $MOCK_SCRIPT --host 127.0.0.1 --port $ENGINE_PORT" \
  --env "BREADBOARD_ENGINE_KEEPALIVE=0" \
  --env "BREADBOARD_MOCK_CLOSE_ON_FINISH=1" \
  --env "V7_KILL_LOG=$KILL_LOG" \
  --env "V7_PROMPT=$PROMPT" \
  --env "V7_WORKSPACE=$ARTIFACT_DIR/dummy_workspace" \
  > "$HARNESS_OUT" 2>&1
HARNESS_STATUS=$?
set -e

set +e
node --import tsx scripts/qc_v7_real_terminal_resume_lane_gate.ts "$ARTIFACT_DIR" "$LANE" "$HARNESS_STATUS"
GATE_STATUS=$?
set -e

if [ -z "${V7_ARTIFACT_DIR:-}" ]; then
  if [ "$GATE_STATUS" -eq 0 ]; then
    rm -rf "$FINAL_GREEN_DIR"
    mkdir -p "$(dirname "$FINAL_GREEN_DIR")"
    mv "$ARTIFACT_DIR" "$FINAL_GREEN_DIR"
    ARTIFACT_DIR="$FINAL_GREEN_DIR"
  else
    rm -rf "$FINAL_RED_DIR"
    mkdir -p "$(dirname "$FINAL_RED_DIR")"
    mv "$ARTIFACT_DIR" "$FINAL_RED_DIR"
    ARTIFACT_DIR="$FINAL_RED_DIR"
  fi
fi

cat <<REPORT
[v7][real-terminal-resume][$LANE] artifact_dir=$ARTIFACT_DIR
[v7][real-terminal-resume][$LANE] harness_status=$HARNESS_STATUS
[v7][real-terminal-resume][$LANE] gate_status=$GATE_STATUS
REPORT

exit "$GATE_STATUS"
