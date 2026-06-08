#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd -- "$ROOT_DIR/.." && pwd)"
cd "$ROOT_DIR"

STAMP="$(date +%Y%m%d_%H%M%S)"
ARTIFACT_DIR="${V7_ARTIFACT_DIR:-$REPO_ROOT/docs_tmp/cli_phase_6/CODESIGN_p14/implementation_validation_v7_engine_lifecycle/V7-H-chaos-flap-endurance/green/live_multiturn_endurance_${STAMP}}"
WORKSPACE="${V7_LIVE_ENDURANCE_WORKSPACE:-/tmp/bb_v7_live_multiturn_endurance_${STAMP}}"
CONFIG_PATH="${V7_LIVE_ENDURANCE_CONFIG:-$REPO_ROOT/agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml}"
COMMAND="${V7_LIVE_ENDURANCE_COMMAND:-bb repl}"
ENGINE_PORT="${V7_LIVE_ENDURANCE_ENGINE_PORT:-$(python - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)}"

rm -rf "$ARTIFACT_DIR" "$WORKSPACE"
mkdir -p "$ARTIFACT_DIR" "$WORKSPACE" "$ARTIFACT_DIR/home"

cat > "$WORKSPACE/README.md" <<'README'
# V7 Live Endurance Workspace

Disposable workspace for BreadBoard TUI lifecycle endurance QC.
README
cat > "$WORKSPACE/notes.md" <<'NOTES'
# Notes

- alpha terminal resizing should not corrupt transcript cells
- beta engine lifecycle messages belong in chrome/status, not chat history
- gamma user prompts must remain visible exactly once
NOTES
cat > "$WORKSPACE/tasks.txt" <<'TASKS'
turn-1 inspect README.md
turn-2 inspect notes.md
turn-3 count files
turn-4 inspect tasks.txt
turn-5 summarize transcript requirement
turn-6 inspect git status
turn-7 create harmless marker
turn-8 inspect marker
turn-9 run wc
turn-10 final summary
TASKS

git -C "$WORKSPACE" init -q
git -C "$WORKSPACE" config user.email test@example.invalid
git -C "$WORKSPACE" config user.name 'BreadBoard V7 Endurance'
git -C "$WORKSPACE" add README.md notes.md tasks.txt
git -C "$WORKSPACE" commit -q -m 'initial endurance workspace'

SCRIPT_PATH="$ARTIFACT_DIR/live_multiturn_endurance.json"
STATE_PATH="$ARTIFACT_DIR/state.ndjson"
SNAPSHOT_PATH="$ARTIFACT_DIR/snapshots.txt"
HARNESS_OUT="$ARTIFACT_DIR/harness_output.txt"

cat > "$ARTIFACT_DIR/case_info.json" <<JSON
{
  "schema_version": "bb.v7.live_multiturn_endurance_case.v1",
  "created_at": "$(date -Is)",
  "artifact_dir": "$ARTIFACT_DIR",
  "workspace": "$WORKSPACE",
  "config_path": "$CONFIG_PATH",
  "command": "$COMMAND",
  "engine_port": "$ENGINE_PORT",
  "repo_root": "$REPO_ROOT",
  "tui_root": "$ROOT_DIR"
}
JSON

cat > "$SCRIPT_PATH" <<'JSON'
[
  { "action": "waitForComposerReady", "timeoutMs": 45000, "mode": "either" },
  { "action": "waitForState", "timeoutMs": 45000, "lifecycleMode": "local-owned", "lifecycleOwned": true, "lifecyclePidPresent": true },
  { "action": "snapshot", "label": "startup", "mode": "history", "maxLines": 180 },

  { "action": "type", "text": "V7 endurance turn 1. Use tools to inspect README.md, then reply with marker V7_ENDURANCE_TURN_1 and one sentence." },
  { "action": "press", "key": "enter", "expectSubmit": true },
  { "action": "waitForState", "timeoutMs": 240000, "fresh": true, "pendingResponse": false, "lastConversationSpeaker": "assistant", "lastConversationPhase": "final" },
  { "action": "snapshot", "label": "turn1-final", "mode": "history", "maxLines": 260 },

  { "action": "waitForComposerReady", "timeoutMs": 30000, "mode": "either" },
  { "action": "type", "text": "V7 endurance turn 2. Use tools to inspect notes.md, then reply with marker V7_ENDURANCE_TURN_2 and one sentence." },
  { "action": "press", "key": "enter", "expectSubmit": true },
  { "action": "waitForState", "timeoutMs": 240000, "fresh": true, "pendingResponse": false, "lastConversationSpeaker": "assistant", "lastConversationPhase": "final" },
  { "action": "resize", "cols": 92, "rows": 34, "delayMs": 400 },
  { "action": "snapshot", "label": "turn2-final-resized-narrow", "mode": "history", "maxLines": 380 },

  { "action": "waitForComposerReady", "timeoutMs": 30000, "mode": "either" },
  { "action": "type", "text": "V7 endurance turn 3. Use tools to count the files in this workspace, then reply with marker V7_ENDURANCE_TURN_3." },
  { "action": "press", "key": "enter", "expectSubmit": true },
  { "action": "waitForState", "timeoutMs": 240000, "fresh": true, "pendingResponse": false, "lastConversationSpeaker": "assistant", "lastConversationPhase": "final" },
  { "action": "resize", "cols": 140, "rows": 42, "delayMs": 400 },
  { "action": "snapshot", "label": "turn3-final-resized-wide", "mode": "history", "maxLines": 500 },

  { "action": "waitForComposerReady", "timeoutMs": 30000, "mode": "either" },
  { "action": "type", "text": "V7 endurance turn 4. Use tools to inspect tasks.txt and identify the turn-4 line. Reply with marker V7_ENDURANCE_TURN_4." },
  { "action": "press", "key": "enter", "expectSubmit": true },
  { "action": "waitForState", "timeoutMs": 240000, "fresh": true, "pendingResponse": false, "lastConversationSpeaker": "assistant", "lastConversationPhase": "final" },
  { "action": "snapshot", "label": "turn4-final", "mode": "history", "maxLines": 620 },

  { "action": "waitForComposerReady", "timeoutMs": 30000, "mode": "either" },
  { "action": "type", "text": "V7 endurance turn 5. Without editing files, explain in one sentence why user prompts must remain visible in the terminal transcript. Include marker V7_ENDURANCE_TURN_5." },
  { "action": "press", "key": "enter", "expectSubmit": true },
  { "action": "waitForState", "timeoutMs": 240000, "fresh": true, "pendingResponse": false, "lastConversationSpeaker": "assistant", "lastConversationPhase": "final" },
  { "action": "press", "key": "ctrl+t", "expectSubmit": false },
  { "action": "waitFor", "text": "transcript", "timeoutMs": 10000, "mode": "frame" },
  { "action": "snapshot", "label": "transcript-viewer-after-turn5", "mode": "frame", "maxLines": 160 },
  { "action": "press", "key": "escape", "expectSubmit": false },
  { "action": "waitForComposerReady", "timeoutMs": 10000, "mode": "either" },

  { "action": "waitForComposerReady", "timeoutMs": 30000, "mode": "either" },
  { "action": "type", "text": "V7 endurance turn 6. Use tools to inspect git status --short. Reply with marker V7_ENDURANCE_TURN_6." },
  { "action": "press", "key": "enter", "expectSubmit": true },
  { "action": "waitForState", "timeoutMs": 240000, "fresh": true, "pendingResponse": false, "lastConversationSpeaker": "assistant", "lastConversationPhase": "final" },
  { "action": "snapshot", "label": "turn6-final", "mode": "history", "maxLines": 760 },

  { "action": "waitForComposerReady", "timeoutMs": 30000, "mode": "either" },
  { "action": "type", "text": "V7 endurance turn 7. Use tools to create v7_endurance_marker.txt containing exactly V7_ENDURANCE_MARKER, then reply with marker V7_ENDURANCE_TURN_7." },
  { "action": "press", "key": "enter", "expectSubmit": true },
  { "action": "waitForState", "timeoutMs": 240000, "fresh": true, "pendingResponse": false, "lastConversationSpeaker": "assistant", "lastConversationPhase": "final" },
  { "action": "snapshot", "label": "turn7-final", "mode": "history", "maxLines": 900 },

  { "action": "waitForComposerReady", "timeoutMs": 30000, "mode": "either" },
  { "action": "type", "text": "V7 endurance turn 8. Use tools to inspect v7_endurance_marker.txt, then reply with marker V7_ENDURANCE_TURN_8." },
  { "action": "press", "key": "enter", "expectSubmit": true },
  { "action": "waitForState", "timeoutMs": 240000, "fresh": true, "pendingResponse": false, "lastConversationSpeaker": "assistant", "lastConversationPhase": "final" },
  { "action": "resize", "cols": 104, "rows": 32, "delayMs": 400 },
  { "action": "snapshot", "label": "turn8-final-resized", "mode": "history", "maxLines": 1040 },

  { "action": "waitForComposerReady", "timeoutMs": 30000, "mode": "either" },
  { "action": "type", "text": "V7 endurance turn 9. Use tools to run wc -l README.md notes.md tasks.txt, then reply with marker V7_ENDURANCE_TURN_9." },
  { "action": "press", "key": "enter", "expectSubmit": true },
  { "action": "waitForState", "timeoutMs": 240000, "fresh": true, "pendingResponse": false, "lastConversationSpeaker": "assistant", "lastConversationPhase": "final" },
  { "action": "snapshot", "label": "turn9-final", "mode": "history", "maxLines": 1180 },

  { "action": "waitForComposerReady", "timeoutMs": 30000, "mode": "either" },
  { "action": "type", "text": "V7 endurance turn 10. Final concise summary: list the files inspected or changed and include marker V7_ENDURANCE_TURN_10." },
  { "action": "press", "key": "enter", "expectSubmit": true },
  { "action": "waitForState", "timeoutMs": 240000, "fresh": true, "pendingResponse": false, "lastConversationSpeaker": "assistant", "lastConversationPhase": "final" },
  { "action": "press", "key": "ctrl+k", "expectSubmit": false },
  { "action": "waitFor", "text": "Models", "timeoutMs": 10000, "mode": "frame" },
  { "action": "snapshot", "label": "model-picker-after-turn10", "mode": "frame", "maxLines": 160 },
  { "action": "press", "key": "escape", "expectSubmit": false },
  { "action": "waitForComposerReady", "timeoutMs": 10000, "mode": "either" },
  { "action": "resize", "cols": 132, "rows": 40, "delayMs": 400 },
  { "action": "snapshot", "label": "turn10-final-history", "mode": "history", "maxLines": 1400 }
]
JSON

set +e
(
  cd "$WORKSPACE"
  BREADBOARD_API_URL="${BREADBOARD_API_URL:-http://127.0.0.1:${ENGINE_PORT}}" \
  BREADBOARD_CLI_PORT="${BREADBOARD_CLI_PORT:-${ENGINE_PORT}}" \
  BREADBOARD_ENGINE_MODE="${BREADBOARD_ENGINE_MODE:-local-owned}" \
  BREADBOARD_ENGINE_KEEPALIVE="${BREADBOARD_ENGINE_KEEPALIVE:-0}" \
  BREADBOARD_PTY_HOME="$ARTIFACT_DIR/home" \
  BREADBOARD_TUI_PROFILE="${BREADBOARD_TUI_PROFILE:-codex_v1}" \
  BREADBOARD_STATE_DUMP_PATH="$STATE_PATH" \
  BREADBOARD_STATE_DUMP_MODE="${BREADBOARD_STATE_DUMP_MODE:-summary}" \
  BREADBOARD_STATE_DUMP_RATE_MS="${BREADBOARD_STATE_DUMP_RATE_MS:-100}" \
  "$ROOT_DIR/node_modules/.bin/tsx" "$ROOT_DIR/scripts/repl_pty_harness.ts" \
    --cmd "$COMMAND" \
    --config "$CONFIG_PATH" \
    --script "$SCRIPT_PATH" \
    --snapshots "$SNAPSHOT_PATH" \
    --cols "${BREADBOARD_QC_COLS:-132}" \
    --rows "${BREADBOARD_QC_ROWS:-40}" \
    --watchdog-ms "${BREADBOARD_QC_WATCHDOG_MS:-300000}" \
    --max-duration-ms "${BREADBOARD_QC_MAX_DURATION_MS:-2400000}" \
    > "$HARNESS_OUT" 2>&1
)
HARNESS_STATUS=$?
set -e

node --import tsx scripts/qc_v7_live_multiturn_endurance_gate.ts "$ARTIFACT_DIR" "$HARNESS_STATUS"
GATE_STATUS=$?

cat <<REPORT
[v7][live-multiturn-endurance] artifact_dir=$ARTIFACT_DIR
[v7][live-multiturn-endurance] harness_status=$HARNESS_STATUS
[v7][live-multiturn-endurance] gate_status=$GATE_STATUS
REPORT

exit "$GATE_STATUS"
