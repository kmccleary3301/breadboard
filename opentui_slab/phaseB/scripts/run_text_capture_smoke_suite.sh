#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACT_DIR="${1:-$ROOT/../../docs_tmp/cli_phase_5/opentui_phaseb_smoke_artifacts}"

mkdir -p "${ARTIFACT_DIR}"

echo "[phaseB-text-suite] artifact dir: ${ARTIFACT_DIR}"
BREADBOARD_OPENTUI_SMOKE_ARTIFACT_DIR="${ARTIFACT_DIR}" bash "${ROOT}/phaseB/scripts/footer_hints_tmux_smoke.sh"
BREADBOARD_OPENTUI_SMOKE_ARTIFACT_DIR="${ARTIFACT_DIR}" bash "${ROOT}/phaseB/scripts/context_burst_tmux_smoke.sh"
BREADBOARD_OPENTUI_SMOKE_ARTIFACT_DIR="${ARTIFACT_DIR}" bash "${ROOT}/phaseB/scripts/artifact_render_tmux_smoke.sh"

echo "[phaseB-text-suite] pass"
