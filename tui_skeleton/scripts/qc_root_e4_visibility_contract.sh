#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

OUT_DIR="${1:-artifacts/root_e4_visibility_contract_manual}"
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

BREADBOARD_PTY_PRESERVE_HOME=1 \
BREADBOARD_TUI_PROFILE="${BREADBOARD_TUI_PROFILE:-codex_v1}" \
pnpm exec tsx scripts/repl_pty_harness.ts \
  --cmd "bb repl" \
  --config ../agent_configs/codex_0-107-0_e4_3-6-2026.yaml \
  --script scripts/root_e4_visibility_contract_pty.json \
  --snapshots "$OUT_DIR/pty_snapshots.txt" \
  --cols "${BREADBOARD_QC_COLS:-120}" \
  --rows "${BREADBOARD_QC_ROWS:-36}" \
  --watchdog-ms 120000 \
  --max-duration-ms 140000

if rg -n "You are Codex|Editing constraints|Presenting your work|Final answer structure|Default: be very concise|File References: When referencing files" "$OUT_DIR/pty_snapshots.txt"; then
  echo "root E4 visibility contract failed: prompt text appeared in visible PTY snapshots" >&2
  exit 1
fi

if ! rg -n "^two$" "$OUT_DIR/pty_snapshots.txt" >/dev/null; then
  echo "root E4 visibility contract failed: expected settled answer line \"two\"" >&2
  exit 1
fi

echo "root E4 visibility contract passed: $OUT_DIR/pty_snapshots.txt"
