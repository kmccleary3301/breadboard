#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TUI_DIR="${ROOT_DIR}/tui_skeleton"

run_lane() {
  local lane="$1"
  local preset="$2"
  local width="$3"
  local ascii="$4"
  local no_color="$5"
  local mode="$6"

  local manifest="ui_baselines/${lane}/manifests/${lane}.yaml"
  local runs_root="ui_baselines/${lane}/_runs_tier2_local"
  local blessed_root="ui_baselines/${lane}/scenarios"

  if [[ "${no_color}" == "1" ]]; then
    export NO_COLOR=1
  else
    unset NO_COLOR || true
  fi
  export BREADBOARD_ASCII="${ascii}"
  export BREADBOARD_TUI_PRESET="${preset}"

  echo "[tier2-local] lane=${lane} preset=${preset} width=${width} mode=${mode}"
  (
    cd "${TUI_DIR}"
    node --import tsx scripts/run_tui_goldens.ts \
      --manifest "${manifest}" \
      --out "${runs_root}" \
      --config ../agent_configs/codex_cli_gpt51mini_e4_live.yaml \
      --max-width "${width}"

    local latest_run
    latest_run="$(ls -td "${runs_root}"/run-* | head -n 1)"

    node --import tsx scripts/compare_tui_goldens.ts \
      --manifest "${manifest}" \
      --candidate "${latest_run}" \
      --blessed-root "${blessed_root}" \
      --summary
  )
}

echo "[tier2-local] installing dependencies"
(cd "${TUI_DIR}" && npm ci)

run_lane "u1" "breadboard_default" "120" "0" "0" "unicode-color"
run_lane "u1" "claude_code_like" "120" "0" "0" "unicode-color"
run_lane "u2" "breadboard_default" "100" "0" "0" "unicode-color"
run_lane "u2" "codex_cli_like" "160" "0" "0" "unicode-color"
run_lane "u3" "claude_code_like" "120" "0" "0" "unicode-color"
run_lane "u3" "codex_cli_like" "160" "0" "0" "unicode-color"

echo "[tier2-local] completed"
