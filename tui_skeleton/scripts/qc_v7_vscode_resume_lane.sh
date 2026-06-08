#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd -- "$ROOT_DIR/.." && pwd)"
cd "$ROOT_DIR"

ARTIFACT_ROOT="$REPO_ROOT/docs_tmp/cli_phase_6/CODESIGN_p14/implementation_validation_v7_engine_lifecycle/V7-G-real-terminal-gauntlet"
RUN_NAME="vscode_resume_after_recovery_run_1"
ARTIFACT_DIR="${V7_ARTIFACT_DIR:-$ARTIFACT_ROOT/work/$RUN_NAME}"
FINAL_GREEN_DIR="${V7_ARTIFACT_DIR:-$ARTIFACT_ROOT/green/$RUN_NAME}"
FINAL_RED_DIR="${V7_ARTIFACT_DIR:-$ARTIFACT_ROOT/red/$RUN_NAME}"

rm -rf "$ARTIFACT_DIR"
mkdir -p "$ARTIFACT_DIR/dummy_workspace" "$ARTIFACT_DIR/home" "$ARTIFACT_DIR/scripts" "$ARTIFACT_DIR/vscode_out"

PROMPT="Trigger V7 vscode real terminal resume recovery."
ENGINE_PORT="${V7_VSCODE_ENGINE_PORT:-19232}"
ENGINE_URL="http://127.0.0.1:${ENGINE_PORT}"
SCENARIO_PATH="$ARTIFACT_DIR/scripts/vscode_resume_after_recovery.json"
WRAPPER_PATH="$ARTIFACT_DIR/scripts/run_repl_with_engine_killer.sh"
KILL_LOG="$ARTIFACT_DIR/kill.log"
HARNESS_OUT="$ARTIFACT_DIR/harness_output.txt"

cat > "$ARTIFACT_DIR/prompt.txt" <<<"$PROMPT"

cat > "$WRAPPER_PATH" <<'EOF_WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
: "${BREADBOARD_STATE_DUMP_PATH:?BREADBOARD_STATE_DUMP_PATH required}"
: "${V7_KILL_LOG:?V7_KILL_LOG required}"
: "${V7_PROMPT:?V7_PROMPT required}"
: "${V7_ROOT_DIR:?V7_ROOT_DIR required}"
: "${V7_WORKSPACE:?V7_WORKSPACE required}"
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
if (!killed) await fs.writeFile(killLog, 'failed_to_find_pending_user_echo_lifecycle_pid=1\n', 'utf8')
NODE
) &
KILLER_PID=$!
cd "$V7_ROOT_DIR"
export BREADBOARD_PTY_HOME="$V7_HOME"
export BREADBOARD_ENGINE_MODE=local-owned
export BREADBOARD_API_URL="$V7_ENGINE_URL"
export BREADBOARD_ENGINE_BIN=node
export BREADBOARD_ENGINE_ARGS="--import tsx tools/mock/mockSseServer.ts --script scripts/mock_sse_v7_real_terminal_hold.json --host 127.0.0.1 --port $V7_ENGINE_PORT"
export BREADBOARD_ENGINE_KEEPALIVE=0
export BREADBOARD_MOCK_CLOSE_ON_FINISH=1
node dist/main.js repl --tui classic --workspace "$V7_WORKSPACE"
STATUS=$?
wait "$KILLER_PID" || true
exit "$STATUS"
EOF_WRAPPER
chmod +x "$WRAPPER_PATH"

cat > "$SCENARIO_PATH" <<JSON
{
  "id": "vscode_v7_resume_after_recovery",
  "terminal": {
    "initialCols": 120,
    "initialRows": 36
  },
  "steps": [
    { "kind": "write", "text": "bash $WRAPPER_PATH\\r" },
    { "kind": "waitForText", "text": "Type your request", "timeoutMs": 30000 },
    { "kind": "write", "text": "$PROMPT\\r" },
    { "kind": "waitForState", "timeoutMs": 60000, "pendingResponse": true, "conversationCountAtLeast": 1 },
    { "kind": "waitForState", "timeoutMs": 60000, "disconnected": true, "statusIncludes": "Recovery needed" },
    { "kind": "screenshot", "label": "vscode-recovery-needed" },
    { "kind": "resize", "cols": 92, "rows": 28 },
    { "kind": "waitForState", "timeoutMs": 60000, "disconnected": true, "statusIncludes": "Recovery needed" },
    { "kind": "waitForStableFrame", "scope": "viewport", "stableMs": 700, "timeoutMs": 10000 },
    { "kind": "screenshot", "label": "vscode-recovery-resized" },
    { "kind": "write", "text": "/resume\\r" },
    { "kind": "waitForState", "timeoutMs": 60000, "disconnected": false, "pendingResponse": false, "statusIncludes": "Recovered", "lastToolEventKind": "status", "lastToolEventTextIncludes": "resume=recovered-new-session" },
    { "kind": "resize", "cols": 92, "rows": 28 },
    { "kind": "waitForStableFrame", "scope": "viewport", "stableMs": 700, "timeoutMs": 10000 },
    {
      "kind": "assert",
      "assertions": [
        { "kind": "contains", "scope": "raw", "text": "$PROMPT" },
        { "kind": "contains", "scope": "raw", "text": "resume=recovered-new-session" },
        { "kind": "notContains", "scope": "buffer", "text": "Lost connection to the engine" },
        { "kind": "notContains", "scope": "buffer", "text": "file://logging/" },
        { "kind": "notContains", "scope": "raw", "text": "V7_REAL_TERMINAL_HOLD_RESPONSE_SHOULD_NOT_APPEAR_BEFORE_KILL" }
      ]
    },
    { "kind": "screenshot", "label": "vscode-resume-recovered" },
    { "kind": "exportBreadBoardState", "label": "vscode-resume-recovered-state" },
    { "kind": "terminate" }
  ],
  "assertions": [
    { "kind": "contains", "scope": "raw", "text": "$PROMPT" },
    { "kind": "contains", "scope": "raw", "text": "resume=recovered-new-session" },
    { "kind": "notContains", "scope": "buffer", "text": "Lost connection to the engine" },
    { "kind": "notContains", "scope": "buffer", "text": "file://logging/" },
    { "kind": "notContains", "scope": "raw", "text": "V7_REAL_TERMINAL_HOLD_RESPONSE_SHOULD_NOT_APPEAR_BEFORE_KILL" }
  ]
}
JSON

set +e
V4_VSCODE_OUT_ROOT="$ARTIFACT_DIR/vscode_out" \
V7_KILL_LOG="$KILL_LOG" \
V7_PROMPT="$PROMPT" \
V7_ROOT_DIR="$ROOT_DIR" \
V7_WORKSPACE="$ARTIFACT_DIR/dummy_workspace" \
V7_HOME="$ARTIFACT_DIR/home" \
V7_ENGINE_URL="$ENGINE_URL" \
V7_ENGINE_PORT="$ENGINE_PORT" \
timeout 240s bash scripts/qc_vscode_terminal_lane.sh "$SCENARIO_PATH" > "$HARNESS_OUT" 2>&1
HARNESS_STATUS=$?
set -e

RUN_DIR="$(tail -1 "$HARNESS_OUT" | tr -d '\r' || true)"
if [ -n "$RUN_DIR" ] && [ -d "$RUN_DIR/artifacts" ]; then
  cp "$RUN_DIR/artifacts/breadboard_artifacts/repl_state.ndjson" "$ARTIFACT_DIR/state.ndjson" 2>/dev/null || true
  cp "$RUN_DIR/artifacts/visible_frames.md" "$ARTIFACT_DIR/snapshots.txt" 2>/dev/null || true
  cp "$RUN_DIR/artifacts/viewport_final.txt" "$ARTIFACT_DIR/visible_text_final.txt" 2>/dev/null || true
  cp "$RUN_DIR/artifacts/scrollback_final.txt" "$ARTIFACT_DIR/scrollback_text_final.txt" 2>/dev/null || true
  cp "$RUN_DIR/artifacts/input_log.ndjson" "$ARTIFACT_DIR/input_log.ndjson" 2>/dev/null || true
  cp "$RUN_DIR/artifacts/terminal_frames.ndjson" "$ARTIFACT_DIR/frames.json" 2>/dev/null || true
  cat > "$ARTIFACT_DIR/metadata.json" <<JSON
{
  "lane": "vscode",
  "integrationMode": "vscode",
  "scenario": "$SCENARIO_PATH",
  "runDir": "$RUN_DIR",
  "harnessStatus": $HARNESS_STATUS
}
JSON
else
  : > "$ARTIFACT_DIR/state.ndjson"
  : > "$ARTIFACT_DIR/snapshots.txt"
  : > "$ARTIFACT_DIR/visible_text_final.txt"
  : > "$ARTIFACT_DIR/scrollback_text_final.txt"
  : > "$ARTIFACT_DIR/input_log.ndjson"
  echo '{"lane":"vscode","integrationMode":"vscode","missingRunDir":true}' > "$ARTIFACT_DIR/metadata.json"
fi

set +e
node --import tsx scripts/qc_v7_real_terminal_resume_lane_gate.ts "$ARTIFACT_DIR" "vscode" "$HARNESS_STATUS"
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
[v7][vscode-resume] artifact_dir=$ARTIFACT_DIR
[v7][vscode-resume] harness_status=$HARNESS_STATUS
[v7][vscode-resume] gate_status=$GATE_STATUS
REPORT

exit "$GATE_STATUS"
