#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: start_tmux_phase4_replay_target.sh --session <breadboard_test_*> [--cols N] [--rows N]
                                         [--tmux-socket NAME] [--port N]
                                         [--tui-preset PRESET] [--config PATH] [--workspace PATH]
                                         [--scrollback-mode <window|scrollback>]
                                         [--landing-always <0|1>]
                                         [--use-dist]
                                         [--attach]

Starts a fixed-size tmux session running the classic Ink TUI plus a local
CLI-bridge engine (uvicorn), suitable as a target for deterministic replay
fixtures driven by tmux capture scenarios.

Defaults are locked for visual replay stability:
- --scrollback-mode scrollback
- --landing-always 1

Safety:
- Refuses to create/kill any session not starting with "breadboard_test_".

Examples:
  scripts/start_tmux_phase4_replay_target.sh --session breadboard_test_phase4_todo --port 9101 --tui-preset claude_code_like
  # CI-friendly (assumes tui_skeleton/dist has been built already):
  scripts/start_tmux_phase4_replay_target.sh --session breadboard_test_phase4_todo --port 9101 --use-dist
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
use_dist=0
scrollback_mode="scrollback"
landing_always="1"

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

log_root="${BREADBOARD_PHASE4_REPLAY_LOG_ROOT:-$repo_root/docs_tmp/tmux_phase4_replay_targets}"
mkdir -p "$log_root/$session"

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

# Choose a TUI entrypoint. In CI we prefer `dist/` so we don't depend on tsx/dev tooling.
if [[ $use_dist -eq 1 ]]; then
  if [[ ! -f "$repo_root/tui_skeleton/dist/main.js" ]]; then
    echo "missing tui_skeleton/dist/main.js (run: cd tui_skeleton && npm run build)" >&2
    exit 2
  fi
  entrypoint_use_dist=(--use-dist)
else
  entrypoint_use_dist=()
fi

pane_cmd=(
  bash
  --noprofile
  --norc
  "$repo_root/scripts/phase4_replay_target_entrypoint.sh"
  --session "$session"
  --port "$port"
  --tui-preset "$tui_preset"
  --config "$config_path"
  --workspace "$workspace"
  --cols "$cols"
  --rows "$rows"
  --scrollback-mode "$scrollback_mode"
  --landing-always "$landing_always"
  "${entrypoint_use_dist[@]}"
)

pane_cmd_str="$(printf '%q ' "${pane_cmd[@]}")"

"${tmux_base[@]}" new-session -d -x "$cols" -y "$rows" -s "$session" "$pane_cmd_str"
"${tmux_base[@]}" set-option -t "$session" history-limit 200000

# Mirror pane output to a file without breaking TTY semantics (critical for Ink TUIs).
# pipe-pane copies output; it does not redirect the process stdout.
pane_target="${session}:0.0"
pane_log="$log_root/$session/pane.log"
pipe_cmd=(bash --noprofile --norc -c "cat >> $(printf '%q' "$pane_log")")
pipe_cmd_str="$(printf '%q ' "${pipe_cmd[@]}")"
("${tmux_base[@]}" pipe-pane -o -t "$pane_target" "$pipe_cmd_str" >/dev/null 2>&1) || true

echo "Started tmux session '$session' ($cols×$rows) on port $port."
if [[ -n "$tmux_socket" ]]; then
  echo "tmux socket: $tmux_socket"
fi
if [[ $attach -eq 1 ]]; then
  "${tmux_base[@]}" attach-session -t "$session"
else
  echo "Attach with: tmux attach-session -t $session"
fi
