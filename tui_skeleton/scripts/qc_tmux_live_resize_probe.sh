#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${CONFIG_PATH:-$ROOT_DIR/../agent_configs/opencode_openrouter_grok4fast_cli_default.yaml}"
WORKSPACE="${WORKSPACE:-/shared_folders/querylake_server/ray_testing/ray_SCE}"
REPLAY_PATH="${REPLAY_PATH:-config/cli_bridge_replays/phase4/subagents_strip_churn_smoke_v1.jsonl}"
OUT_DIR="${OUT_DIR:-$ROOT_DIR/scripts}"
SESSION="bb_qc_live_resize_$RANDOM"
TARGET="${SESSION}:0.0"
ITERATIONS="${ITERATIONS:-28}"

mkdir -p "$OUT_DIR"

cleanup() {
  tmux kill-session -t "$SESSION" >/dev/null 2>&1 || true
}
trap cleanup EXIT

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

tmux new-session -d -s "$SESSION" -x 132 -y 40 \
  "cd '$ROOT_DIR' && BREADBOARD_REPL_CLEAR_SCREEN=1 BREADBOARD_SCROLLBACK_REPLAY_ON_RESIZE=1 BREADBOARD_TUI_SCROLLBACK_REPLAY_ON_RESIZE=1 node dist/main.js repl --tui classic --workspace '$WORKSPACE' --config '$CONFIG_PATH'"

sleep 2

tmux send-keys -t "$TARGET" C-u
tmux send-keys -t "$TARGET" "replay:${REPLAY_PATH}"
tmux send-keys -t "$TARGET" Enter
sleep 0.15

SIZES=(
  "92 26"
  "142 44"
  "100 30"
  "120 35"
  "86 25"
  "132 40"
)

for ((i=1; i<=ITERATIONS; i++)); do
  size="${SIZES[$(((i - 1) % ${#SIZES[@]}))]}"
  cols="$(awk '{print $1}' <<<"$size")"
  rows="$(awk '{print $2}' <<<"$size")"
  tmux resize-pane -t "$TARGET" -x "$cols" -y "$rows"
  sleep 0.12
done

# Return to baseline and wait for replay to finish.
tmux resize-pane -t "$TARGET" -x 132 -y 40
sleep 2

OUT_FILE="$OUT_DIR/_tmp_tmux_live_resize_probe_$(date +%s).txt"
tmux capture-pane -p -S -1800 -t "$TARGET" >"$OUT_FILE"

if ! rg -q "Subagent strip churn smoke complete" "$OUT_FILE"; then
  fail "live replay did not complete under resize churn (capture=$OUT_FILE)"
fi

landing_count="$(count_matches '╭── BreadBoard' "$OUT_FILE" fixed)"
if [[ "$landing_count" -gt 1 ]]; then
  fail "duplicate landing frame headers detected after live resize churn (capture=$OUT_FILE)"
fi

if rg -ni -e 'phase4 replay target|\[phase4-replay-target\]|launching TUI|PATH=|TERM=screen|BREADBOARD_[A-Z_]+=|session=tmux' "$OUT_FILE" >/dev/null; then
  fail "debug/env leakage detected in live resize capture (capture=$OUT_FILE)"
fi

echo "capture=$OUT_FILE iterations=$ITERATIONS landing_headers=$landing_count replay=$REPLAY_PATH"
echo "[qc] PASS: tmux live resize probe completed without landing duplication or leakage"

