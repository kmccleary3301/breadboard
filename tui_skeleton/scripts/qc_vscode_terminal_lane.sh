#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"
SCENARIO="${1:-$ROOT_DIR/tools/vscode-terminal-harness/scenarios/startup_smoke.json}"
OUT_ROOT="${P15_VSCODE_OUT_ROOT:-${V4_VSCODE_OUT_ROOT:-$REPO_ROOT/docs_tmp/cli_phase_6/CODESIGN_p15/implementation_validation_p15_peer_parity_host_universality/artifacts/vscode}}"
SCENARIO="$(readlink -m "$SCENARIO")"
OUT_ROOT="$(readlink -m "$OUT_ROOT")"
STAMP="$(date +%Y%m%d-%H%M%S)-$$-${RANDOM:-0}"
RUN_DIR="$OUT_ROOT/vscode_harness_smoke_$STAMP"
RUNTIME_ROOT="${BREADBOARD_VSCODE_RUNTIME_ROOT:-$(mktemp -d /tmp/bb-vscode-harness.XXXXXX)}"
WORKSPACE="$RUNTIME_ROOT/workspace"
USER_DATA="$RUNTIME_ROOT/vscode-user-data"
EXT_DIR="$RUNTIME_ROOT/vscode-extensions"
HARNESS_HOME="$RUNTIME_ROOT/home"
ARTIFACT_DIR="$RUN_DIR/artifacts"
LOG_FILE="$RUN_DIR/code.log"
CODE_BIN="${CODE_BIN:-code}"
USE_XVFB="${BREADBOARD_VSCODE_USE_XVFB:-auto}"
read -r -a CODE_EXTRA_ARGS <<< "${BREADBOARD_VSCODE_EXTRA_ARGS:-}"
ENGINE_PORT="$(python3 - <<'PY'
import socket
s = socket.socket()
s.bind(('127.0.0.1', 0))
print(s.getsockname()[1])
s.close()
PY
)"
ENGINE_URL="http://127.0.0.1:$ENGINE_PORT"

if [[ "${CODE_BIN}" == "code" ]]; then
  RESOLVED_CODE="$(command -v code || true)"
  if [[ "${RESOLVED_CODE}" == *"/remote-cli/"* && -x /snap/bin/code ]]; then
    CODE_BIN="/snap/bin/code"
  fi
fi

mkdir -p "$OUT_ROOT" "$RUN_DIR" "$WORKSPACE" "$USER_DATA/User" "$EXT_DIR" "$ARTIFACT_DIR" "$HARNESS_HOME"
cat > "$WORKSPACE/README.md" <<'WORKSPACE_EOF'
# BreadBoard VSCode Harness Workspace

This workspace is generated for P15 VSCode integrated-terminal validation.
WORKSPACE_EOF
mkdir -p "$WORKSPACE/.vscode"
cat > "$WORKSPACE/.vscode/settings.json" <<'SETTINGS_EOF'
{
  "files.watcherExclude": {
    "**": true,
    "**/.git/**": true,
    "**/node_modules/**": true,
    "**/dist/**": true,
    "**/target/**": true,
    "**/.cache/**": true
  },
  "search.exclude": {
    "**": true
  },
  "terminal.integrated.scrollback": 10000,
  "terminal.integrated.shellIntegration.enabled": false,
  "terminal.integrated.stickyScroll.enabled": false,
  "telemetry.telemetryLevel": "off",
  "extensions.autoCheckUpdates": false,
  "extensions.autoUpdate": false,
  "update.mode": "none"
}
SETTINGS_EOF
cp "$WORKSPACE/.vscode/settings.json" "$USER_DATA/User/settings.json"

cat > "$RUN_DIR/launch_env.json" <<EOF_JSON
{
  "codeBin": "$CODE_BIN",
  "scenario": "$SCENARIO",
  "runtimeRoot": "$RUNTIME_ROOT",
  "workspace": "$WORKSPACE",
  "artifactDir": "$ARTIFACT_DIR",
  "userDataDir": "$USER_DATA",
  "extensionsDir": "$EXT_DIR",
  "harnessHome": "$HARNESS_HOME",
  "engineUrl": "$ENGINE_URL",
  "extensionDevelopmentPath": "$ROOT_DIR/tools/vscode-terminal-harness"
}
EOF_JSON
cat > "$RUN_DIR/launcher_command.txt" <<EOF_CMD
$CODE_BIN --new-window --wait --user-data-dir=$USER_DATA --extensions-dir=$EXT_DIR --disable-gpu --disable-chromium-sandbox --disable-workspace-trust --skip-welcome --skip-release-notes --sync=off --log=warn --extensionDevelopmentPath=$ROOT_DIR/tools/vscode-terminal-harness $WORKSPACE
EOF_CMD
python3 - "$RUN_DIR/watcher_diagnostics_before.json" <<'PY_DIAG'
import json, os, sys
def read(path):
    try:
        return int(open(path).read().strip())
    except Exception:
        return None
instances = 0
processes = 0
for pid in os.listdir('/proc'):
    if not pid.isdigit():
        continue
    try:
        count = 0
        for fd in os.listdir(f'/proc/{pid}/fd'):
            try:
                if 'anon_inode:inotify' in os.readlink(f'/proc/{pid}/fd/{fd}'):
                    count += 1
            except OSError:
                pass
        if count:
            instances += count
            processes += 1
    except Exception:
        pass
json.dump({
    "maxUserWatches": read('/proc/sys/fs/inotify/max_user_watches'),
    "maxUserInstances": read('/proc/sys/fs/inotify/max_user_instances'),
    "maxQueuedEvents": read('/proc/sys/fs/inotify/max_queued_events'),
    "activeInotifyInstancesObserved": instances,
    "activeInotifyProcessesObserved": processes
}, open(sys.argv[1], 'w'), indent=2)
PY_DIAG

CODE_CMD=(
"$CODE_BIN"
  --new-window \
  --wait \
  "--user-data-dir=$USER_DATA" \
  "--extensions-dir=$EXT_DIR" \
  --disable-gpu \
  --disable-chromium-sandbox \
  --disable-workspace-trust \
  --skip-welcome \
  --skip-release-notes \
  --sync=off \
  --log=warn \
  "--extensionDevelopmentPath=$ROOT_DIR/tools/vscode-terminal-harness" \
  "${CODE_EXTRA_ARGS[@]}" \
  "$WORKSPACE"
)

if { [ "$USE_XVFB" = "auto" ] && [ -z "${DISPLAY:-}" ]; } || [ "$USE_XVFB" = "1" ]; then
  CODE_CMD=(xvfb-run -a -s "-screen 0 1280x900x24" "${CODE_CMD[@]}")
fi

set +e
(
  flock 9
  BREADBOARD_VSCODE_HARNESS_SCENARIO="$SCENARIO" \
  BREADBOARD_VSCODE_HARNESS_ARTIFACT_DIR="$ARTIFACT_DIR" \
  BREADBOARD_REPO_ROOT="$REPO_ROOT" \
  BREADBOARD_TUI_ROOT="$ROOT_DIR" \
  BREADBOARD_ENGINE_ROOT="$REPO_ROOT" \
  BREADBOARD_ENGINE_MODE=local-owned \
  BREADBOARD_ENGINE_KEEPALIVE=0 \
  BREADBOARD_API_URL="$ENGINE_URL" \
  BREADBOARD_CLI_PORT="$ENGINE_PORT" \
  HOME="$HARNESS_HOME" \
  PATH="$HOME/.local/bin:$PATH" \
  env -u VSCODE_IPC_HOOK_CLI "${CODE_CMD[@]}" >"$LOG_FILE" 2>&1
) 9>"$OUT_ROOT/.vscode_harness.lock"
CODE_EXIT=$?
set -e

echo "$CODE_EXIT" > "$RUN_DIR/code_exit.txt"
python3 - "$RUN_DIR/watcher_diagnostics_after.json" <<'PY_DIAG'
import json, os, sys
def read(path):
    try:
        return int(open(path).read().strip())
    except Exception:
        return None
instances = 0
processes = 0
for pid in os.listdir('/proc'):
    if not pid.isdigit():
        continue
    try:
        count = 0
        for fd in os.listdir(f'/proc/{pid}/fd'):
            try:
                if 'anon_inode:inotify' in os.readlink(f'/proc/{pid}/fd/{fd}'):
                    count += 1
            except OSError:
                pass
        if count:
            instances += count
            processes += 1
    except Exception:
        pass
json.dump({
    "maxUserWatches": read('/proc/sys/fs/inotify/max_user_watches'),
    "maxUserInstances": read('/proc/sys/fs/inotify/max_user_instances'),
    "maxQueuedEvents": read('/proc/sys/fs/inotify/max_queued_events'),
    "activeInotifyInstancesObserved": instances,
    "activeInotifyProcessesObserved": processes
}, open(sys.argv[1], 'w'), indent=2)
PY_DIAG
if [ -d "$USER_DATA/logs" ]; then
  mkdir -p "$RUN_DIR/vscode-user-logs"
  cp -R "$USER_DATA/logs/." "$RUN_DIR/vscode-user-logs/" 2>/dev/null || true
fi
if [ -f "$ARTIFACT_DIR/verdict.json" ]; then
  cp "$ARTIFACT_DIR/verdict.json" "$RUN_DIR/verdict.json"
fi
if [ -f "$ARTIFACT_DIR/summary.md" ]; then
  cp "$ARTIFACT_DIR/summary.md" "$RUN_DIR/summary.md"
fi

if [ "$CODE_EXIT" -ne 0 ]; then
  cat > "$RUN_DIR/summary.md" <<EOF_SUMMARY
# VSCode Harness Launch Failure

Status: red

VSCode exited with code $CODE_EXIT.

See: $LOG_FILE
EOF_SUMMARY
  echo "$RUN_DIR"
  exit "$CODE_EXIT"
fi

if [ ! -f "$ARTIFACT_DIR/verdict.json" ]; then
  cat > "$RUN_DIR/verdict_missing.json" <<EOF_VERDICT
{
  "ok": false,
  "kind": "missing-verdict",
  "codeExit": $CODE_EXIT,
  "logFile": "$LOG_FILE",
  "watcherDiagnosticsBefore": "$RUN_DIR/watcher_diagnostics_before.json",
  "watcherDiagnosticsAfter": "$RUN_DIR/watcher_diagnostics_after.json"
}
EOF_VERDICT
  cat > "$RUN_DIR/summary.md" <<EOF_SUMMARY
# VSCode Harness Missing Verdict

Status: red

VSCode exited successfully but no verdict was emitted.

See: $LOG_FILE
See: $RUN_DIR/verdict_missing.json
EOF_SUMMARY
  echo "$RUN_DIR"
  exit 2
fi

if ! node -e "const v=require(process.argv[1]); if(!v.ok){console.error(JSON.stringify(v,null,2)); process.exit(3)}" "$ARTIFACT_DIR/verdict.json"; then
  echo "$RUN_DIR"
  exit 3
fi
echo "$RUN_DIR"
