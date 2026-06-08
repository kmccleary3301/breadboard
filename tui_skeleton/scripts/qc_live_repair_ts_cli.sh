#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd -- "$ROOT_DIR/.." && pwd)"
cd "$ROOT_DIR"

STAMP="$(date +%Y%m%d_%H%M%S)"
CASE_ARG="${1:-artifacts/live_repair_ts_cli_${STAMP}}"
if [[ "$CASE_ARG" = /* ]]; then
  CASE_DIR="$CASE_ARG"
else
  CASE_DIR="$REPO_ROOT/$CASE_ARG"
fi
WORKSPACE="${BREADBOARD_LIVE_REPAIR_TS_WORKSPACE:-/tmp/bb_live_repair_ts_cli_${STAMP}}"
CONFIG_PATH="${BREADBOARD_LIVE_REPAIR_TS_CONFIG:-$REPO_ROOT/agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml}"
COMMAND="${BREADBOARD_LIVE_REPAIR_TS_COMMAND:-bb repl}"
SCRIPT_PATH="$ROOT_DIR/scripts/live_repair_ts_cli_pty.json"
LOG_ROOT="$REPO_ROOT/logging"
ENGINE_PORT="${BREADBOARD_LIVE_REPAIR_TS_ENGINE_PORT:-$(python - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)}"

rm -rf "$CASE_DIR" "$WORKSPACE"
mkdir -p "$CASE_DIR" "$WORKSPACE/src"
touch "$CASE_DIR/start.marker"

cat > "$WORKSPACE/README.md" <<'README'
# Tiny TypeScript CLI Fixture

This disposable project contains one parser bug. Fix it without touching parent directories.
README
cat > "$WORKSPACE/AGENTS.md" <<'AGENTS'
# Tiny TypeScript CLI Fixture Workspace

This disposable repo is the complete workspace. Do not inspect or modify parent directories.
Run the provided smoke test after fixing the bug.
AGENTS
cat > "$WORKSPACE/package.json" <<'JSON'
{
  "name": "bb-live-repair-ts-cli",
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "smoke": "bash smoke_test.sh"
  }
}
JSON
cat > "$WORKSPACE/src/args.ts" <<'TS'
export interface CliOptions {
  name: string;
  shout: boolean;
}

export function parseArgs(argv: string[]): CliOptions {
  const result = { name: "world", shout: false };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--name") {
      result.name = arg;
    } else if (arg === "--shout") {
      result.shout = true;
    }
  }
  return result;
}

export function renderGreeting(options: CliOptions): string {
  const text = `hello, ${options.name}`;
  return options.shout ? text.toUpperCase() : text;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  console.log(renderGreeting(parseArgs(process.argv.slice(2))));
}
TS
cat > "$WORKSPACE/smoke_test.sh" <<'SMOKE'
#!/usr/bin/env bash
set -euo pipefail
actual="$(node src/args.ts --name Ada --shout)"
if [[ "$actual" != "HELLO, ADA" ]]; then
  echo "expected HELLO, ADA, got $actual" >&2
  exit 1
fi
plain="$(node src/args.ts --name Linus)"
if [[ "$plain" != "hello, Linus" ]]; then
  echo "expected hello, Linus, got $plain" >&2
  exit 1
fi
echo "tiny-ts-cli-smoke-ok"
SMOKE
chmod +x "$WORKSPACE/smoke_test.sh"

git -C "$WORKSPACE" init -q
git -C "$WORKSPACE" config user.email test@example.invalid
git -C "$WORKSPACE" config user.name 'BreadBoard Tiny TS Repair Gate'
git -C "$WORKSPACE" add README.md AGENTS.md package.json src/args.ts smoke_test.sh
git -C "$WORKSPACE" commit -q -m 'initial broken tiny ts cli project'

cat > "$CASE_DIR/case_info.json" <<JSON
{
  "schema_version": "bb.live_repair_ts_cli_case.v1",
  "created_at": "$(date -Is)",
  "case_dir": "$CASE_DIR",
  "workspace": "$WORKSPACE",
  "config_path": "$CONFIG_PATH",
  "command": "$COMMAND",
  "script_path": "$SCRIPT_PATH",
  "repo_root": "$REPO_ROOT",
  "tui_root": "$ROOT_DIR"
}
JSON

(
  cd "$WORKSPACE"
  BREADBOARD_PTY_PRESERVE_HOME="${BREADBOARD_PTY_PRESERVE_HOME:-1}" \
  BREADBOARD_API_URL="${BREADBOARD_API_URL:-http://127.0.0.1:${ENGINE_PORT}}" \
  BREADBOARD_CLI_PORT="${BREADBOARD_CLI_PORT:-${ENGINE_PORT}}" \
  BREADBOARD_ENGINE_MODE="${BREADBOARD_ENGINE_MODE:-local-owned}" \
  BREADBOARD_ENGINE_KEEPALIVE="${BREADBOARD_ENGINE_KEEPALIVE:-0}" \
  BREADBOARD_TUI_PROFILE="${BREADBOARD_TUI_PROFILE:-codex_v1}" \
  BREADBOARD_STATE_DUMP_PATH="$CASE_DIR/repl_state.ndjson" \
  BREADBOARD_STATE_DUMP_MODE="${BREADBOARD_STATE_DUMP_MODE:-summary}" \
  BREADBOARD_STATE_DUMP_RATE_MS="${BREADBOARD_STATE_DUMP_RATE_MS:-100}" \
  "$ROOT_DIR/node_modules/.bin/tsx" "$ROOT_DIR/scripts/repl_pty_harness.ts" \
    --cmd "$COMMAND" \
    --config "$CONFIG_PATH" \
    --script "$SCRIPT_PATH" \
    --snapshots "$CASE_DIR/pty_snapshots.txt" \
    --cols "${BREADBOARD_QC_COLS:-132}" \
    --rows "${BREADBOARD_QC_ROWS:-40}" \
    --watchdog-ms "${BREADBOARD_QC_WATCHDOG_MS:-180000}" \
    --max-duration-ms "${BREADBOARD_QC_MAX_DURATION_MS:-900000}"
)

LOG_DIR=""
if [ -d "$LOG_ROOT" ]; then
  LOG_DIR="$(find "$LOG_ROOT" -mindepth 1 -maxdepth 1 -type d -newer "$CASE_DIR/start.marker" -name "*$(basename "$WORKSPACE")*" -printf '%T@ %p\n' 2>/dev/null | sort -nr | awk 'NR==1 { $1=""; sub(/^ /, ""); print }')"
fi
if [ -n "$LOG_DIR" ]; then
  printf '%s\n' "$LOG_DIR" > "$CASE_DIR/logging_dir.txt"
fi

node --import tsx tools/assertions/liveRepairTsCliCheck.ts --case-dir "$CASE_DIR" --strict
