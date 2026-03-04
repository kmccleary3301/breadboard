#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${CONFIG_PATH:-$ROOT_DIR/../agent_configs/opencode_openrouter_grok4fast_cli_default.yaml}"
OUT_DIR="${OUT_DIR:-$ROOT_DIR/scripts}"
SESSION="bb_qc_resize_$RANDOM"
TARGET="${SESSION}:0.0"

mkdir -p "$OUT_DIR"

cleanup() {
  tmux kill-session -t "$SESSION" >/dev/null 2>&1 || true
}
trap cleanup EXIT

tmux new-session -d -s "$SESSION" -x 130 -y 38 \
  "cd '$ROOT_DIR' && BREADBOARD_REPL_CLEAR_SCREEN=1 BREADBOARD_SCROLLBACK_REPLAY_ON_RESIZE=1 BREADBOARD_TUI_SCROLLBACK_REPLAY_ON_RESIZE=1 node dist/main.js repl --tui classic --workspace /shared_folders/querylake_server/ray_testing/ray_SCE --config '$CONFIG_PATH'"

sleep 3

for size in "95 28" "140 42" "100 30" "132 40" "88 26" "130 38"; do
  cols="$(awk '{print $1}' <<<"$size")"
  rows="$(awk '{print $2}' <<<"$size")"
  tmux resize-pane -t "$TARGET" -x "$cols" -y "$rows"
  sleep 0.6
done

OUT_FILE="$OUT_DIR/_tmp_tmux_resize_probe_$(date +%s).txt"
tmux capture-pane -p -S -800 -t "$TARGET" >"$OUT_FILE"

landing_count="$(rg -o '╭── BreadBoard' "$OUT_FILE" | wc -l | tr -d ' ')"
prompt_count="$(rg -o '❯ Try \"refactor <filepath>\"' "$OUT_FILE" | wc -l | tr -d ' ')"

echo "capture=$OUT_FILE landing_headers=$landing_count prompt_rows=$prompt_count"

if [[ "$landing_count" -gt 1 ]]; then
  echo "[qc] FAIL: duplicate landing frame headers detected in tmux history capture"
  exit 1
fi

echo "[qc] PASS: tmux resize probe did not detect duplicate landing frame headers"
