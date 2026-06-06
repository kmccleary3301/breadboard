#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TUI_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
P16_ROOT="${TUI_ROOT}/../docs_tmp/cli_phase_6/CODESIGN_p16/implementation_validation_p16_final_design_complete"
STAMP="${P16_CORE_UX_STAMP:-$(date +%Y%m%d-%H%M%S)}"
ARTIFACT_DIR="${P16_ROOT}/artifacts/core_ux/slash_composer_${STAMP}"

mkdir -p "${ARTIFACT_DIR}"

SNAPSHOTS="${ARTIFACT_DIR}/slash_composer_snapshots.txt"
RAW_OUTPUT="${ARTIFACT_DIR}/slash_composer_raw_output.txt"
HARNESS_LOG="${ARTIFACT_DIR}/slash_composer_harness.log"
GATE_JSON="${ARTIFACT_DIR}/p16_slash_composer_gate.json"

cd "${TUI_ROOT}"

bash scripts/run_legacy_pty_case.sh \
  --script scripts/p16_core_ux_slash_composer_pty.json \
  --snapshots "${SNAPSHOTS}" \
  --raw-output "${RAW_OUTPUT}" \
  --cols 112 \
  --rows 32 \
  --max-duration-ms 180000 \
  >"${HARNESS_LOG}" 2>&1

node --import tsx scripts/p16_core_ux_slash_composer_gate.ts "${SNAPSHOTS}" "${GATE_JSON}"

echo "P16 slash/composer gate passed: ${GATE_JSON}"
