#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TUI_DIR="${ROOT_DIR}/tui_skeleton"

run_report_combo() {
  local preset="$1"
  local ascii="$2"
  local no_color="$3"
  local label="$4"

  if [[ "${no_color}" == "1" ]]; then
    export NO_COLOR=1
    unset FORCE_COLOR || true
  else
    unset NO_COLOR || true
  fi
  export BREADBOARD_ASCII="${ascii}"
  export BREADBOARD_TUI_PRESET="${preset}"

  echo "[tier1-local] report combo preset=${preset} mode=${label}"
  (cd "${TUI_DIR}" && node --import tsx scripts/tui_u1_goldens_report.ts)
  (cd "${TUI_DIR}" && node --import tsx scripts/tui_u2_goldens_report.ts)
  (cd "${TUI_DIR}" && node --import tsx scripts/tui_u3_goldens_report.ts)
}

echo "[tier1-local] installing dependencies"
(cd "${TUI_DIR}" && npm ci)

run_report_combo "breadboard_default" "0" "0" "unicode-color"
run_report_combo "claude_code_like" "0" "0" "unicode-color"
run_report_combo "breadboard_default" "1" "1" "ascii-no-color"
run_report_combo "claude_code_like" "1" "1" "ascii-no-color"

unset NO_COLOR || true
export BREADBOARD_ASCII=0
export BREADBOARD_TUI_PRESET=breadboard_default

echo "[tier1-local] strict gates"
(cd "${TUI_DIR}" && node --import tsx scripts/tui_u1_goldens_strict.ts)
(cd "${TUI_DIR}" && node --import tsx scripts/tui_u2_goldens_strict.ts)
(cd "${TUI_DIR}" && node --import tsx scripts/tui_u3_goldens_strict.ts)

echo "[tier1-local] completed"
