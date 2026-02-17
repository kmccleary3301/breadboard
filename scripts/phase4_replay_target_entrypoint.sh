#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: phase4_replay_target_entrypoint.sh --session NAME --port N
                                         [--tui-preset PRESET]
                                         [--config PATH]
                                         [--workspace PATH]
                                         [--cols N] [--rows N]
                                         [--scrollback-mode <window|scrollback>]
                                         [--landing-always <0|1>]
                                         [--use-dist]

Runs inside a tmux pane. Starts the local CLI-bridge engine and then launches
the Ink TUI in "classic" mode, emitting clear markers into the pane so CI
captures are never blank when something goes wrong.

Defaults are locked for replay visual stability:
- --scrollback-mode scrollback
- --landing-always 1

Logs:
- Writes a combined pane transcript to:
    $BREADBOARD_PHASE4_REPLAY_LOG_ROOT/<session>/pane.log
- Writes CLI-bridge logs to:
    $BREADBOARD_PHASE4_REPLAY_LOG_ROOT/<session>/cli_bridge.log
EOF
  exit 1
}

session=""
port=""
tui_preset="claude_code_like"
config_path="agent_configs/opencode_openrouter_grok4fast_cli_default.yaml"
workspace=""
cols=""
rows=""
use_dist=0
scrollback_mode="scrollback"
landing_always="1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) shift; session="${1:-}";;
    --port) shift; port="${1:-}";;
    --tui-preset) shift; tui_preset="${1:-}";;
    --config) shift; config_path="${1:-}";;
    --workspace) shift; workspace="${1:-}";;
    --cols) shift; cols="${1:-}";;
    --rows) shift; rows="${1:-}";;
    --scrollback-mode) shift; scrollback_mode="${1:-}";;
    --landing-always) shift; landing_always="${1:-}";;
    --use-dist) use_dist=1;;
    --help|-h) usage;;
    *) echo "Unknown arg: $1" >&2; usage;;
  esac
  shift
done

if [[ -n "$scrollback_mode" && "$scrollback_mode" != "window" && "$scrollback_mode" != "scrollback" ]]; then
  echo "Invalid --scrollback-mode '$scrollback_mode' (expected window|scrollback)." >&2
  exit 2
fi
if [[ -n "$landing_always" && "$landing_always" != "0" && "$landing_always" != "1" ]]; then
  echo "Invalid --landing-always '$landing_always' (expected 0|1)." >&2
  exit 2
fi

if [[ -z "$session" || -z "$port" ]]; then
  echo "Missing --session or --port" >&2
  usage
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="${workspace:-$repo_root}"
workspace="$(cd "$workspace" && pwd)"

log_root="${BREADBOARD_PHASE4_REPLAY_LOG_ROOT:-$repo_root/docs_tmp/tmux_phase4_replay_targets}"
log_dir="$log_root/$session"
mkdir -p "$log_dir"

echo "[phase4 replay target] starting"
echo "[phase4 replay target] session=$session port=$port preset=$tui_preset use_dist=$use_dist"
echo "[phase4 replay target] repo_root=$repo_root"
echo "[phase4 replay target] workspace=$workspace"
echo "[phase4 replay target] PATH=$PATH"
echo "[phase4 replay target] TERM=${TERM:-}"
echo "[phase4 replay target] CI=${CI:-}"
echo "[phase4 replay target] TMUX=${TMUX:-}"

# tmux can start detached sessions with stdin not connected to the pane pty.
# Ink needs a real tty stdin; rebind to /dev/tty when possible.
if [[ ! -t 0 ]]; then
  if [[ -r /dev/tty ]]; then
    exec </dev/tty
  fi
fi

# CI runners sometimes end up with odd tty sizing; pin it when requested.
if [[ -n "$cols" && -n "$rows" ]]; then
  stty cols "$cols" rows "$rows" 2>/dev/null || true
fi
echo "[phase4 replay target] stty_size=$(stty size 2>/dev/null || echo unknown)"
echo "[phase4 replay target] stty_cols_rows=$(stty -a 2>/dev/null | sed -n 's/.*\\(columns [0-9][0-9]*; rows [0-9][0-9]*;\\).*/\\1/p' || true)"

echo "[phase4 replay target] python=$(command -v python || true)"
python --version || true
echo "[phase4 replay target] node=$(command -v node || true)"
node --version || true
echo "[phase4 replay target] npm=$(command -v npm || true)"
npm --version || true
echo "[phase4 replay target] bash_stdin_isatty=$([ -t 0 ] && echo true || echo false)"
node -e 'console.log("[phase4 replay target] node_tty","stdout.isTTY="+Boolean(process.stdout&&process.stdout.isTTY),"stdin.isTTY="+Boolean(process.stdin&&process.stdin.isTTY),"cols="+String(process.stdout&&process.stdout.columns),"rows="+String(process.stdout&&process.stdout.rows),"TERM="+String(process.env.TERM||""))' || true

# Favor a well-supported TERM in CI so Ink's cursor control works predictably.
if [[ -n "${CI:-}" ]]; then
  export TERM="${TERM_OVERRIDE_FOR_CI:-xterm-256color}"
fi

export BREADBOARD_TUI_SUPPRESS_MAINTENANCE=1
export RAY_SCE_LOCAL_MODE="${RAY_SCE_LOCAL_MODE:-1}"
export BREADBOARD_CLI_HOST=127.0.0.1
export BREADBOARD_CLI_PORT="$port"
export BREADBOARD_API_URL="http://127.0.0.1:$port"
export BREADBOARD_STREAM_SCHEMA=2
export BREADBOARD_STREAM_INCLUDE_LEGACY=0
if [[ -n "$scrollback_mode" ]]; then
  export BREADBOARD_TUI_MODE="$scrollback_mode"
fi
if [[ -n "$landing_always" ]]; then
  export BREADBOARD_TUI_LANDING_ALWAYS="$landing_always"
fi

echo "[phase4 replay target] launching cli_bridge"
cd "$repo_root"
python -m agentic_coder_prototype.api.cli_bridge.server >"$log_dir/cli_bridge.log" 2>&1 &
server_pid=$!

cleanup() {
  kill "$server_pid" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

for i in $(seq 1 80); do
  if curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
    echo "[phase4 replay target] cli_bridge health check complete"
    break
  fi
  if [[ $i -eq 80 ]]; then
    echo "[phase4 replay target] cli_bridge health check failed (timeout)"
    echo "[phase4 replay target] tail cli_bridge.log:"
    tail -n 80 "$log_dir/cli_bridge.log" || true
    exit 3
  fi
  sleep 0.1
done

cd "$repo_root/tui_skeleton"

if [[ $use_dist -eq 1 ]]; then
  if [[ ! -f "$repo_root/tui_skeleton/dist/main.js" ]]; then
    echo "[phase4 replay target] missing tui_skeleton/dist/main.js (run: cd tui_skeleton && npm run build)"
    exit 2
  fi
  tui_cmd=(node dist/main.js repl)
else
  tui_cmd=(npm run dev -- repl)
fi

# Keep transcript content in the live Ink pane by default; explicit mode override
# (BREADBOARD_TUI_MODE or --scrollback-mode) wins when requested.
if [[ -z "${BREADBOARD_TUI_MODE:-}" ]]; then
  export BREADBOARD_TUI_SCROLLBACK="${BREADBOARD_TUI_SCROLLBACK:-0}"
fi

echo "[phase4 replay target] launching TUI"
echo "[phase4 replay target] cmd=${tui_cmd[*]} --tui classic --tui-preset $tui_preset --config $config_path --workspace $workspace"
echo "[phase4 replay target] tui_mode=${BREADBOARD_TUI_MODE:-<unset>} tui_scrollback=${BREADBOARD_TUI_SCROLLBACK:-<unset>} landing_always=${BREADBOARD_TUI_LANDING_ALWAYS:-<unset>}"

# Many terminal UI libraries (including Ink) intentionally degrade or disable
# dynamic rendering when they detect CI, even if stdout is a tty (tmux pane).
# For tmux capture scenarios we want full interactive rendering.
unset CI
unset GITHUB_ACTIONS
unset NO_COLOR
export FORCE_COLOR="${FORCE_COLOR:-3}"
export CLICOLOR_FORCE="${CLICOLOR_FORCE:-1}"
export COLORTERM="${COLORTERM:-truecolor}"
export BREADBOARD_TMUX_E2E=1

"${tui_cmd[@]}" --tui classic --tui-preset "$tui_preset" --config "$config_path" --workspace "$workspace"

echo ""
echo "[phase4 replay target exited]"

# Keep the pane open for post-mortem capture.
exec bash
