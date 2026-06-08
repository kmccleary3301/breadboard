#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG_PATH=""
if [[ "${1:-}" == "--config" ]]; then
  CONFIG_PATH="${2:-}"
elif [[ -n "${1:-}" ]]; then
  CONFIG_PATH="$1"
fi
SESSION="${P4_GHOSTTY_TMUX_SESSION:-p4ghostty}"
TARGET="${P4_GHOSTTY_TMUX_TARGET:-${SESSION}:0.0}"
BOOT_DELAY_MS="${P4_GHOSTTY_BOOT_DELAY_MS:-3200}"
FIRST_DELAY_MS="${P4_GHOSTTY_FIRST_DELAY_MS:-2200}"
SECOND_DELAY_MS="${P4_GHOSTTY_SECOND_DELAY_MS:-9000}"
PROMPT_1="${P4_GHOSTTY_PROMPT:-}"
PROMPT_2="${P4_GHOSTTY_SECOND_PROMPT:-}"
TMUX_COLS="${P4_GHOSTTY_TMUX_COLS:-132}"
TMUX_ROWS="${P4_GHOSTTY_TMUX_ROWS:-36}"
PERMISSION_MODE="${P4_GHOSTTY_PERMISSION_MODE:-}"
WORKSPACE_CWD="${BREADBOARD_QC_WORKSPACE_CWD:-/tmp/bb_qc_ghostty_tmux_workspace}"
if [[ "$WORKSPACE_CWD" != /* ]]; then
  WORKSPACE_CWD="$ROOT_DIR/$WORKSPACE_CWD"
fi

cleanup() {
  tmux kill-session -t "$SESSION" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [[ -z "$CONFIG_PATH" ]]; then
  echo "ghosttyTmuxSession.sh requires a config path argument" >&2
  exit 1
fi

build_launch_command() {
  cat <<CMD
mkdir -p $(printf %q "$WORKSPACE_CWD")
cd $(printf %q "$WORKSPACE_CWD")
export BREADBOARD_API_URL=$(printf %q "${BREADBOARD_API_URL:-}")
export BREADBOARD_STATE_DUMP_PATH=$(printf %q "${BREADBOARD_STATE_DUMP_PATH:-}")
export BREADBOARD_STATE_DUMP_MODE=$(printf %q "${BREADBOARD_STATE_DUMP_MODE:-summary}")
export BREADBOARD_STATE_DUMP_RATE_MS=$(printf %q "${BREADBOARD_STATE_DUMP_RATE_MS:-100}")
export BREADBOARD_QC_BATCH_ID=$(printf %q "${BREADBOARD_QC_BATCH_ID:-}")
export BREADBOARD_QC_CASE_ID=$(printf %q "${BREADBOARD_QC_CASE_ID:-}")
export BREADBOARD_TUI_VIEWPORT_RESETS_FILE=$(printf %q "${BREADBOARD_TUI_VIEWPORT_RESETS_FILE:-}")
export BREADBOARD_TUI_SURFACE_MODEL_FILE=$(printf %q "${BREADBOARD_TUI_SURFACE_MODEL_FILE:-}")
export BREADBOARD_TUI_SCROLLBACK_FEED_FILE=$(printf %q "${BREADBOARD_TUI_SCROLLBACK_FEED_FILE:-}")
export BREADBOARD_TUI_RENDER_TIMELINE_FILE=$(printf %q "${BREADBOARD_TUI_RENDER_TIMELINE_FILE:-}")
export BREADBOARD_TUI_APP_START_ANCHOR_FILE=$(printf %q "${BREADBOARD_TUI_APP_START_ANCHOR_FILE:-}")
export BREADBOARD_TUI_MANAGED_REGION_BOUNDS_FILE=$(printf %q "${BREADBOARD_TUI_MANAGED_REGION_BOUNDS_FILE:-}")
export BREADBOARD_TUI_SCROLLBACK_CLAUSE_VERDICTS_FILE=$(printf %q "${BREADBOARD_TUI_SCROLLBACK_CLAUSE_VERDICTS_FILE:-}")
export BREADBOARD_ENGINE_MODE=$(printf %q "${BREADBOARD_ENGINE_MODE:-}")
export MOCK_API_KEY=$(printf %q "${MOCK_API_KEY:-}")
export BREADBOARD_TUI_LIVE_SHELL_MODE=$(printf %q "${BREADBOARD_TUI_LIVE_SHELL_MODE:-}")
export BREADBOARD_TUI_OWNERSHIP_MODE=$(printf %q "${BREADBOARD_TUI_OWNERSHIP_MODE:-}")
export BREADBOARD_TUI_LIVE_SHELL_HOST=$(printf %q "${BREADBOARD_TUI_LIVE_SHELL_HOST:-}")
export BREADBOARD_TUI_SCENE_OWNED_STRATEGY=$(printf %q "${BREADBOARD_TUI_SCENE_OWNED_STRATEGY:-}")
export BREADBOARD_TUI_RENDERER_STRATEGY=$(printf %q "${BREADBOARD_TUI_RENDERER_STRATEGY:-}")
export BREADBOARD_STREAM_MAX_RETRIES=$(printf %q "${BREADBOARD_STREAM_MAX_RETRIES:-}")
export BREADBOARD_STREAM_STALL_TIMEOUT_MS=$(printf %q "${BREADBOARD_STREAM_STALL_TIMEOUT_MS:-}")
export BREADBOARD_PTY_PRESERVE_HOME=$(printf %q "${BREADBOARD_PTY_PRESERVE_HOME:-}")
export BREADBOARD_QC_WORKSPACE_CWD=$(printf %q "$WORKSPACE_CWD")
if [[ -n "${P4_GHOSTTY_PRE_APP_TEXT:-}" ]]; then
  printf "%b" "${P4_GHOSTTY_PRE_APP_TEXT:-}"
fi
if [[ -n $(printf %q "$PERMISSION_MODE") ]]; then
  exec node $(printf %q "$ROOT_DIR/dist/main.js") repl --config $(printf %q "$CONFIG_PATH") --permission-mode $(printf %q "$PERMISSION_MODE")
fi
exec node $(printf %q "$ROOT_DIR/dist/main.js") repl --config $(printf %q "$CONFIG_PATH")
CMD
}

tmux kill-session -t "$SESSION" >/dev/null 2>&1 || true

tmux new-session -d -s "$SESSION" -x "$TMUX_COLS" -y "$TMUX_ROWS" "bash -lc $(printf %q "$(build_launch_command)")"

sleep "$(python - <<PY
print(${BOOT_DELAY_MS}/1000)
PY
)"

(
  sleep "$(python - <<PY
print(${FIRST_DELAY_MS}/1000)
PY
)"
  if [[ -n "$PROMPT_1" ]]; then
    tmux send-keys -t "$TARGET" C-u
    tmux send-keys -t "$TARGET" -- "$PROMPT_1"
    tmux send-keys -t "$TARGET" Enter
  fi
  if [[ -n "$PROMPT_2" ]]; then
    sleep "$(python - <<PY
print(${SECOND_DELAY_MS}/1000)
PY
)"
    tmux send-keys -t "$TARGET" C-u
    tmux send-keys -t "$TARGET" -- "$PROMPT_2"
    tmux send-keys -t "$TARGET" Enter
  fi
) &

tmux attach -t "$TARGET"
