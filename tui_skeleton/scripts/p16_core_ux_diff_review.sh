#!/usr/bin/env bash
set -euo pipefail

STAMP="${P16_CORE_UX_STAMP:-$(date +%Y%m%d-%H%M%S)}"
P16_ROOT="${P16_ROOT:-../docs_tmp/cli_phase_6/CODESIGN_p16/implementation_validation_p16_final_design_complete}"
ROOT="${P16_CORE_UX_ROOT:-$P16_ROOT/artifacts/tools_permissions_diff/diff_review_${STAMP}}"
WORKSPACE="${ROOT}/dummy_git_workspace"
mkdir -p "$ROOT" "$WORKSPACE"

git -C "$WORKSPACE" init >/dev/null
git -C "$WORKSPACE" config user.email "breadboard@example.test"
git -C "$WORKSPACE" config user.name "BreadBoard Test"
mkdir -p "$WORKSPACE/src"
cat > "$WORKSPACE/src/app.ts" <<'TS'
export const value = 1
TS
cat > "$WORKSPACE/src/server.c" <<'C'
int main(void) {
  return 0;
}
C
git -C "$WORKSPACE" add src/app.ts src/server.c
git -C "$WORKSPACE" commit -m initial >/dev/null
cat > "$WORKSPACE/src/app.ts" <<'TS'
export const value = 2
export const added = "phase-f"
TS
cat > "$WORKSPACE/src/server.c" <<'C'
int main(void) {
  return 1;
}
C
cat > "$WORKSPACE/notes.txt" <<'TXT'
untracked note
TXT

NESTED="$WORKSPACE/nested_repo"
mkdir -p "$NESTED"
git -C "$NESTED" init >/dev/null
git -C "$NESTED" config user.email "breadboard@example.test"
git -C "$NESTED" config user.name "BreadBoard Test"
cat > "$NESTED/nested.c" <<'C'
int nested(void) {
  return 0;
}
C
git -C "$NESTED" add nested.c
git -C "$NESTED" commit -m nested-initial >/dev/null
cat > "$NESTED/nested.c" <<'C'
int nested(void) {
  return 7;
}
C

SNAPSHOTS="$ROOT/diff_review_snapshots.txt"
RAW="$ROOT/diff_review_raw.ansi"
HARNESS_LOG="$ROOT/diff_review_harness.log"
GATE_JSON="$ROOT/p16_diff_review_gate.json"
STATE="$ROOT/repl_state.ndjson"
CLIPBOARD_SINK="$ROOT/fake_clipboard_patch.txt"
NESTED_SNAPSHOTS="$ROOT/diff_review_nested_snapshots.txt"
NESTED_RAW="$ROOT/diff_review_nested_raw.ansi"
NESTED_LOG="$ROOT/diff_review_nested_harness.log"
NESTED_STATE="$ROOT/repl_nested_state.ndjson"

BREADBOARD_FAKE_CLIPBOARD_WRITE_PATH="$CLIPBOARD_SINK" \
BREADBOARD_STATE_DUMP_PATH="$STATE" \
BREADBOARD_STATE_DUMP_MODE=summary \
BREADBOARD_STATE_DUMP_RATE_MS=100 \
bash scripts/run_legacy_pty_case.sh \
  --script scripts/p16_core_ux_diff_review_pty.json \
  --cmd "node dist/main.js repl --tui classic --workspace $WORKSPACE" \
  --config ../agent_configs/misc/opencode_mock_c_fs.yaml \
  --snapshots "$SNAPSHOTS" \
  --raw-output "$RAW" \
  --cols 120 \
  --rows 34 \
  --max-duration-ms 180000 \
  > "$HARNESS_LOG" 2>&1

BREADBOARD_STATE_DUMP_PATH="$NESTED_STATE" \
BREADBOARD_STATE_DUMP_MODE=summary \
BREADBOARD_STATE_DUMP_RATE_MS=100 \
bash scripts/run_legacy_pty_case.sh \
  --script scripts/p16_core_ux_diff_review_nested_pty.json \
  --cmd "node dist/main.js repl --tui classic --workspace $NESTED" \
  --config ../agent_configs/misc/opencode_mock_c_fs.yaml \
  --snapshots "$NESTED_SNAPSHOTS" \
  --raw-output "$NESTED_RAW" \
  --cols 120 \
  --rows 34 \
  --max-duration-ms 180000 \
  > "$NESTED_LOG" 2>&1

node --import tsx scripts/p16_core_ux_diff_review_gate.ts "$SNAPSHOTS" "$GATE_JSON"
if ! grep -q "diff --git a/src/server.c b/src/server.c" "$WORKSPACE/.breadboard/review.patch"; then
  echo "exported patch missing C diff" >&2
  exit 1
fi
if ! grep -q "diff --git a/src/server.c b/src/server.c" "$CLIPBOARD_SINK"; then
  echo "fake clipboard patch missing C diff" >&2
  exit 1
fi
if ! grep -q "nested.c" "$NESTED_SNAPSHOTS"; then
  echo "nested git diff snapshot missing nested.c" >&2
  exit 1
fi
printf 'P16 diff review gate passed: %s\n' "$ROOT"
