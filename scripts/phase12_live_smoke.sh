#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

CONFIG_PATH="${CONFIG_PATH:-agent_configs/opencode_mock_c_fs.yaml}"
WORKSPACE="${WORKSPACE:-${ROOT_DIR}/agent_ws_smoke_local}"
DOCTOR_TIMEOUT_S="${DOCTOR_TIMEOUT_S:-60}"
RUN_TIMEOUT_S="${RUN_TIMEOUT_S:-120}"

export MOCK_API_KEY="${MOCK_API_KEY:-dummy}"
export BREADBOARD_ENGINE_KEEPALIVE="${BREADBOARD_ENGINE_KEEPALIVE:-0}"
export BREADBOARD_ENGINE_MODE="${BREADBOARD_ENGINE_MODE:-auto}"
export RAY_DISABLE_DASHBOARD="${RAY_DISABLE_DASHBOARD:-1}"

TUI_DIR="${ROOT_DIR}/tui_skeleton"

echo "[phase12-smoke] building TUI"
pushd "${TUI_DIR}" >/dev/null
npm run build
popd >/dev/null

echo "[phase12-smoke] doctor"
timeout "${DOCTOR_TIMEOUT_S}s" node "${TUI_DIR}/dist/main.js" doctor --config "${CONFIG_PATH}"

echo "[phase12-smoke] run"
timeout "${RUN_TIMEOUT_S}s" node "${TUI_DIR}/dist/main.js" run --config "${CONFIG_PATH}" --workspace "${WORKSPACE}" "Smoke test: say hi and exit."

echo "[phase12-smoke] ok"

