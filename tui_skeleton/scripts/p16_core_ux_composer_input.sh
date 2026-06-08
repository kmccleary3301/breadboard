#!/usr/bin/env bash
set -euo pipefail
STAMP="${P16_CORE_UX_STAMP:-$(date +%Y%m%d-%H%M%S)}"
ROOT="${P16_CORE_UX_ROOT:-../docs_tmp/cli_phase_6/CODESIGN_p16/implementation_validation_p16_final_design_complete/artifacts/core_ux/composer_input_${STAMP}}"
mkdir -p "$ROOT"
SNAPSHOTS="$ROOT/composer_input_snapshots.txt"
RAW="$ROOT/composer_input_raw_output.txt"
HARNESS_LOG="$ROOT/composer_input_harness.log"
GATE_JSON="$ROOT/p16_composer_input_gate.json"

bash scripts/run_legacy_pty_case.sh \
  --script scripts/p16_core_ux_composer_input_pty.json \
  --snapshots "$SNAPSHOTS" \
  --raw-output "$RAW" \
  --max-duration-ms 180000 \
  > "$HARNESS_LOG" 2>&1

node --import tsx scripts/p16_core_ux_composer_input_gate.ts "$SNAPSHOTS" "$GATE_JSON"
printf 'P16 composer input gate passed: %s\n' "$ROOT"
