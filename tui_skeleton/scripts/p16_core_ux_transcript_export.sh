#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
P16_ROOT="${P16_ROOT:-$ROOT_DIR/../docs_tmp/cli_phase_6/CODESIGN_p16/implementation_validation_p16_final_design_complete}"
STAMP="${P16_CORE_UX_STAMP:-$(date +%Y%m%d-%H%M%S)}"
OUT_DIR="$P16_ROOT/artifacts/core_ux/transcript_save_p16_$STAMP"
mkdir -p "$OUT_DIR"
cd "$ROOT_DIR"

BREADBOARD_STATE_DUMP_PATH="$OUT_DIR/repl_state.ndjson" \
BREADBOARD_STATE_DUMP_MODE=summary \
BREADBOARD_STATE_DUMP_RATE_MS=100 \
bash scripts/run_legacy_pty_case.sh \
  --script scripts/p16_transcript_save_export_pty.json \
  --config ../agent_configs/misc/opencode_mock_c_fs.yaml \
  --snapshots "$OUT_DIR/pty_snapshots.txt" \
  --raw-output "$OUT_DIR/pty_raw.ansi" \
  --max-duration-ms 240000 | tee "$OUT_DIR/run.log"

node --import tsx scripts/p16_transcript_save_export_gate.ts --case-dir "$OUT_DIR" | tee "$OUT_DIR/p16_transcript_save_export_gate.json"

echo "$OUT_DIR"
