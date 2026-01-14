#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<EOF
Usage: $0 [--cli <binary>] [--session <name>] [--workspace <path>] [--cols <n>] [--rows <n>] [--fifo <path>] [--attach]

Starts a fixed-size tmux session that runs a CLAUDE/Codex-compatible CLI (default: breadboard).

Options:
  --cli <binary>       CLI to run inside tmux (default: breadboard)
  --session <name>     tmux session name (default: breadboard-live)
  --workspace <path>   Workspace directory to cd into before launching (default: current directory)
  --cols <n>           Pane width in columns (default: 110)
  --rows <n>           Pane height in rows (default: 40)
  --fifo <path>        Named pipe path for the input bus (default: /tmp/<session>-input.fifo)
  --attach             Attach to the tmux session after launching
  --help               Show this help text
EOF
  exit 1
}

session="breadboard-live"
cli="breadboard"
workspace="$(pwd)"
cols=110
rows=40
attach=0
cli_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cli)
      shift
      cli="$1"
      ;;
    --session)
      shift
      session="$1"
      ;;
    --workspace)
      shift
      workspace="$1"
      ;;
    --cols)
      shift
      cols="$1"
      ;;
    --rows)
      shift
      rows="$1"
      ;;
    --fifo)
      shift
      fifo="$1"
      ;;
    --attach)
      attach=1
      ;;
    --)
      shift
      cli_args+=("$@")
      break
      ;;
    --help)
      usage
      ;;
    *)
      cli_args+=("$1")
      ;;
  esac
  shift
done

workspace="$(cd "$workspace" && pwd)"
fifo="${fifo:-/tmp/${session}-input.fifo}"
state_dir="${workspace}/artifacts/interactive/${session}"
mkdir -p "$state_dir"

if ! command -v tmux >/dev/null; then
  echo "tmux is required but not installed." >&2
  exit 1
fi

if tmux has-session -t "$session" &>/dev/null; then
  echo "A tmux session named '$session' already exists, killing it."
  tmux kill-session -t "$session"
fi

if [[ -e "$fifo" && ! -p "$fifo" ]]; then
  rm -f "$fifo"
fi

if [[ ! -p "$fifo" ]]; then
  mkfifo "$fifo"
fi

env_line="KYLECODE_STATE_DUMP_PATH='${state_dir}/repl_state.ndjson' KYLECODE_STATE_DUMP_MODE='summary' KYLECODE_STATE_DUMP_RATE_MS='1000'"
cli_path="$(command -v "$cli" 2>/dev/null || echo "$cli")"

quoted_args=""
for arg in "${cli_args[@]}"; do
  quoted_args+="$(printf ' %q' "$arg")"
done

tmux new-session -d -s "$session" -x "$cols" -y "$rows" \
  "bash -lc 'set +e; set +u; set +o pipefail; stty -ixon 2>/dev/null || true; cd \"$workspace\" && $env_line \"$cli_path\"$quoted_args; printf \"\\n[cli exited]\\n\"; exec bash'"

tmux set-option -t "$session" history-limit 200000

echo "Started tmux session '$session' ($cols×$rows) running '$cli'."
echo "Input FIFO: $fifo"
echo "State dump: ${state_dir}/repl_state.ndjson"
if [[ $attach -eq 1 ]]; then
  tmux attach-session -t "$session"
else
  echo "Attach with: tmux attach-session -t $session"
fi
