#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${CONFIG_PATH:-$ROOT_DIR/../agent_configs/misc/opencode_openrouter_grok4fast_cli_default.yaml}"
OUT_DIR="${OUT_DIR:-$ROOT_DIR/scripts}"
ITERATIONS="${ITERATIONS:-10}"
SESSION="bb_qc_chaos_$RANDOM"
TARGET="${SESSION}:0.0"
WORKSPACE="${WORKSPACE:-/shared_folders/querylake_server/ray_testing/ray_SCE}"

mkdir -p "$OUT_DIR"

cleanup() {
  tmux kill-session -t "$SESSION" >/dev/null 2>&1 || true
}
trap cleanup EXIT

SIZES=(
  "130 38"
  "95 28"
  "140 42"
  "88 26"
  "120 34"
  "102 30"
  "132 40"
)

CMDS=(
  "/files"
  "/models"
  "/mo"
  "/todos"
  "/to"
  "/tasks"
  "/ta"
  "/config"
  "/co"
  "/skills"
  "/sk"
  "/usage"
)

tmux new-session -d -s "$SESSION" -x 130 -y 38 \
  "cd '$ROOT_DIR' && BREADBOARD_REPL_CLEAR_SCREEN=1 BREADBOARD_SCROLLBACK_REPLAY_ON_RESIZE=1 BREADBOARD_TUI_SCROLLBACK_REPLAY_ON_RESIZE=1 node dist/main.js repl --tui classic --workspace '$WORKSPACE' --config '$CONFIG_PATH'"

sleep 3

fail() {
  echo "[qc] FAIL: $1"
  exit 1
}

count_matches() {
  local pattern="$1"
  local file="$2"
  local mode="${3:-regex}"
  local count
  if [[ "$mode" == "fixed" ]]; then
    count="$(rg -oF "$pattern" "$file" 2>/dev/null | wc -l | tr -d ' ' || true)"
  else
    count="$(rg -o "$pattern" "$file" 2>/dev/null | wc -l | tr -d ' ' || true)"
  fi
  if [[ -z "$count" ]]; then
    count="0"
  fi
  echo "$count"
}

for ((i=1; i<=ITERATIONS; i++)); do
  size_index=$((RANDOM % ${#SIZES[@]}))
  cmd_index=$((RANDOM % ${#CMDS[@]}))
  size="${SIZES[$size_index]}"
  cmd="${CMDS[$cmd_index]}"

  cols="$(awk '{print $1}' <<<"$size")"
  rows="$(awk '{print $2}' <<<"$size")"
  tmux resize-pane -t "$TARGET" -x "$cols" -y "$rows"
  sleep 0.35

  tmux send-keys -t "$TARGET" C-u
  tmux send-keys -t "$TARGET" "$cmd"
  tmux send-keys -t "$TARGET" Enter
  sleep 0.45
  tmux send-keys -t "$TARGET" Escape
  sleep 0.2

  VIEW_FILE="$OUT_DIR/_tmp_tmux_chaos_view_iter${i}_$$.txt"
  tmux capture-pane -p -t "$TARGET" >"$VIEW_FILE"

  landing_count="$(count_matches '╭── BreadBoard' "$VIEW_FILE" fixed)"
  tips_count="$(count_matches '(?i)Tips for getting started' "$VIEW_FILE")"
  prompt_count="$(count_matches '❯ Try \"refactor <filepath>\"' "$VIEW_FILE")"

  if [[ "$landing_count" -gt 1 ]]; then
    fail "duplicate landing frame headers in active viewport (iter=$i cmd=$cmd size=$size file=$VIEW_FILE)"
  fi
  if [[ "$tips_count" -gt 1 ]]; then
    fail "duplicate landing tips block in active viewport (iter=$i cmd=$cmd size=$size file=$VIEW_FILE)"
  fi
  if [[ "$prompt_count" -gt 2 ]]; then
    fail "composer prompt duplicated unexpectedly (iter=$i cmd=$cmd size=$size file=$VIEW_FILE)"
  fi
done

OUT_FILE="$OUT_DIR/_tmp_tmux_chaos_probe_$(date +%s).txt"
tmux capture-pane -p -S -1200 -t "$TARGET" >"$OUT_FILE"

landing_history_count="$(count_matches '╭── BreadBoard' "$OUT_FILE" fixed)"
tips_history_count="$(count_matches '(?i)Tips for getting started' "$OUT_FILE")"

echo "capture=$OUT_FILE iterations=$ITERATIONS landing_headers_history=$landing_history_count tips_history=$tips_history_count"
echo "[qc] PASS: tmux chaos probe did not detect viewport duplication artifacts"
