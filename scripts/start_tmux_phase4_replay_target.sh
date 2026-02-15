#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: start_tmux_phase4_replay_target.sh --session <breadboard_test_*> [--cols N] [--rows N]
                                         [--tmux-socket NAME] [--port N]
                                         [--tui-preset PRESET] [--config PATH] [--workspace PATH]
                                         [--attach]

Starts a fixed-size tmux session running the classic Ink TUI plus a local
CLI-bridge engine (uvicorn), suitable as a target for deterministic replay
fixtures driven by tmux capture scenarios.

Safety:
- Refuses to create/kill any session not starting with "breadboard_test_".

Examples:
  scripts/start_tmux_phase4_replay_target.sh --session breadboard_test_phase4_todo --port 9101 --tui-preset claude_code_like
  python scripts/run_tmux_capture_scenario.py --target breadboard_test_phase4_todo:0.0 --scenario phase4_replay/todo_preview_v1 --actions config/tmux_scenario_actions/phase4_replay/todo_preview_v1.json
EOF
  exit 1
}

session=""
cols=160
rows=45
attach=0
tmux_socket=""
port=""
tui_preset="claude_code_like"
config_path="agent_configs/opencode_openrouter_grok4fast_cli_default.yaml"
workspace=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) shift; session="${1:-}";;
    --cols) shift; cols="${1:-}";;
    --rows) shift; rows="${1:-}";;
    --attach) attach=1;;
    --tmux-socket) shift; tmux_socket="${1:-}";;
    --port) shift; port="${1:-}";;
    --tui-preset) shift; tui_preset="${1:-}";;
    --config) shift; config_path="${1:-}";;
    --workspace) shift; workspace="${1:-}";;
    --help|-h) usage;;
    *) echo "Unknown arg: $1" >&2; usage;;
  esac
  shift
done

if [[ -z "$session" ]]; then
  echo "Missing --session" >&2
  usage
fi
if [[ "$session" != breadboard_test_* ]]; then
  echo "Refusing to start session '$session' (must start with breadboard_test_)." >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="${workspace:-$repo_root}"
workspace="$(cd "$workspace" && pwd)"

if ! command -v tmux >/dev/null; then
  echo "tmux is required but not installed." >&2
  exit 1
fi

tmux_base=(tmux)
if [[ -n "$tmux_socket" ]]; then
  tmux_base=(tmux -L "$tmux_socket")
fi

if "${tmux_base[@]}" has-session -t "$session" &>/dev/null; then
  "${tmux_base[@]}" kill-session -t "$session"
fi

# Default to a stable non-9099 port to avoid clobbering developer sessions.
if [[ -z "$port" ]]; then
  # Small deterministic-ish hash: sum of session chars modulo 200 -> [9100, 9299]
  sum=0
  for (( i=0; i<${#session}; i++ )); do
    c="${session:i:1}"
    sum=$((sum + $(printf "%d" "'$c")))
  done
  port=$((9100 + (sum % 200)))
fi

cmd="cd \"$repo_root\" && \
export BREADBOARD_TUI_SUPPRESS_MAINTENANCE=1 && \
RAY_SCE_LOCAL_MODE=1 \
BREADBOARD_CLI_HOST=127.0.0.1 \
BREADBOARD_CLI_PORT=\"$port\" \
BREADBOARD_API_URL=\"http://127.0.0.1:$port\" \
BREADBOARD_STREAM_SCHEMA=2 \
BREADBOARD_STREAM_INCLUDE_LEGACY=0 \
python -m agentic_coder_prototype.api.cli_bridge.server >\"/tmp/breadboard_cli_bridge_${session}.log\" 2>&1 & \
server_pid=\$!; \
cleanup() { kill \"\$server_pid\" >/dev/null 2>&1 || true; }; \
trap cleanup EXIT INT TERM; \
for i in {1..60}; do curl -fsS \"http://127.0.0.1:$port/health\" >/dev/null 2>&1 && break; sleep 0.1; done; \
cd tui_skeleton && \
# Keep transcript content in the live Ink pane; scrollback mode prints transcript
# entries as static feed lines that often scroll out of the visible tmux viewport.
BREADBOARD_API_URL=\"http://127.0.0.1:$port\" \
BREADBOARD_STREAM_SCHEMA=2 \
BREADBOARD_STREAM_INCLUDE_LEGACY=0 \
BREADBOARD_TUI_SCROLLBACK=0 \
npm run dev -- repl --tui classic --tui-preset \"$tui_preset\" --config \"$config_path\" --workspace \"$workspace\"; \
printf \"\\n[phase4 replay target exited]\\n\"; \
exec bash"

"${tmux_base[@]}" new-session -d -x "$cols" -y "$rows" -s "$session" "bash -lc 'set +e; set +u; set +o pipefail; stty -ixon 2>/dev/null || true; $cmd'"
"${tmux_base[@]}" set-option -t "$session" history-limit 200000

echo "Started tmux session '$session' ($cols×$rows) on port $port."
if [[ -n "$tmux_socket" ]]; then
  echo "tmux socket: $tmux_socket"
fi
if [[ $attach -eq 1 ]]; then
  "${tmux_base[@]}" attach-session -t "$session"
else
  echo "Attach with: tmux attach-session -t $session"
fi
