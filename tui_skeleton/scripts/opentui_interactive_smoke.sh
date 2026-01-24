#!/usr/bin/env bash
set -euo pipefail

# Launch OpenTUI briefly, wait for controller readiness markers, then shut down cleanly.
# Writes artifacts under docs_tmp/cli_phase_3/artifacts/opentui_phaseE_smoke/<timestamp>/.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACT_ROOT="${OPENTUI_SMOKE_ARTIFACT_DIR:-${ROOT_DIR}/../docs_tmp/cli_phase_3/artifacts/opentui_phaseE_smoke}"
TS="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="${ARTIFACT_ROOT}/${TS}"
READY_FILE="${RUN_DIR}/controller_ready.ndjson"
LOG_FILE="${RUN_DIR}/opentui_smoke.log"
TIMEOUT_S="${OPENTUI_SMOKE_TIMEOUT_S:-20}"

mkdir -p "${RUN_DIR}"

pushd "${ROOT_DIR}" >/dev/null

export BREADBOARD_CONTROLLER_READY_FILE="${READY_FILE}"
export BREADBOARD_TUI_MODE="opentui"
export BREADBOARD_ENGINE_PREFER_BUNDLE="0"

node node_modules/tsx/dist/cli.mjs src/main.ts repl --tui opentui >"${LOG_FILE}" 2>&1 &
PID=$!

have_ready=0
deadline=$((SECONDS + TIMEOUT_S))
while (( SECONDS < deadline )); do
  if [[ -f "${READY_FILE}" ]]; then
    if grep -q '"label":"bridge_ready"' "${READY_FILE}" && grep -q '"label":"ui_connected"' "${READY_FILE}"; then
      have_ready=1
      break
    fi
  fi
  sleep 0.1
done

if [[ "${have_ready}" != "1" ]]; then
  echo "[opentui-smoke] readiness markers missing after ${TIMEOUT_S}s" >&2
  kill -TERM "${PID}" 2>/dev/null || true
  wait "${PID}" 2>/dev/null || true
  exit 1
fi

kill -INT "${PID}" 2>/dev/null || true
wait "${PID}" 2>/dev/null || true

popd >/dev/null

echo "[opentui-smoke] ok: ${RUN_DIR}"

