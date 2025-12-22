#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logging"
LOG_FILE="${LOG_DIR}/cli_bridge.log"

mkdir -p "${LOG_DIR}"

export PYTHONPATH="${ROOT_DIR}:${PYTHONPATH:-}"

HOST="${BREADBOARD_CLI_HOST:-127.0.0.1}"
PORT="${BREADBOARD_CLI_PORT:-9099}"
LATENCY_MS="${BREADBOARD_CLI_LATENCY_MS:-0}"
JITTER_MS="${BREADBOARD_CLI_JITTER_MS:-0}"
DROP_RATE="${BREADBOARD_CLI_DROP_RATE:-0}"
export MOCK_API_KEY="${MOCK_API_KEY:-dummy}"

MOCK_SSE_SCRIPT="${BREADBOARD_CLI_MOCK_SSE_SCRIPT:-}"
MOCK_SSE_ONLY="${BREADBOARD_CLI_MOCK_SSE_ONLY:-0}"
MOCK_SSE_HOST="${BREADBOARD_CLI_MOCK_SSE_HOST:-127.0.0.1}"
MOCK_SSE_PORT="${BREADBOARD_CLI_MOCK_SSE_PORT:-9191}"
MOCK_SSE_LOOP="${BREADBOARD_CLI_MOCK_SSE_LOOP:-0}"
MOCK_SSE_DELAY_MULTIPLIER="${BREADBOARD_CLI_MOCK_SSE_DELAY_MULTIPLIER:-1}"
MOCK_SSE_JITTER_MS="${BREADBOARD_CLI_MOCK_SSE_JITTER_MS:-0}"
MOCK_SSE_DROP_RATE="${BREADBOARD_CLI_MOCK_SSE_DROP_RATE:-0}"

cleanup() {
  if [[ -n "${MOCK_SSE_PID:-}" ]]; then
    kill "${MOCK_SSE_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [[ -n "${MOCK_SSE_SCRIPT}" ]]; then
  RESOLVED_SCRIPT="${MOCK_SSE_SCRIPT}"
  if [[ "${RESOLVED_SCRIPT}" != /* ]]; then
    RESOLVED_SCRIPT="${ROOT_DIR}/${RESOLVED_SCRIPT}"
  fi
  echo "[cli-bridge] Launching deterministic mock SSE server on ${MOCK_SSE_HOST}:${MOCK_SSE_PORT}"
  echo "[cli-bridge] Script: ${RESOLVED_SCRIPT}"
  MOCK_SSE_ARGS=(
    "tsx"
    "tui_skeleton/tools/mock/mockSseServer.ts"
    "--script"
    "${RESOLVED_SCRIPT}"
    "--host"
    "${MOCK_SSE_HOST}"
    "--port"
    "${MOCK_SSE_PORT}"
    "--delay-multiplier"
    "${MOCK_SSE_DELAY_MULTIPLIER}"
  )
  if [[ "${MOCK_SSE_LOOP}" == "1" ]]; then
    MOCK_SSE_ARGS+=("--loop")
  fi
  if [[ "${MOCK_SSE_JITTER_MS}" != "0" ]]; then
    MOCK_SSE_ARGS+=("--jitter-ms" "${MOCK_SSE_JITTER_MS}")
  fi
  if [[ "${MOCK_SSE_DROP_RATE}" != "0" ]]; then
    MOCK_SSE_ARGS+=("--drop-rate" "${MOCK_SSE_DROP_RATE}")
  fi
  "${MOCK_SSE_ARGS[@]}" >>"${LOG_FILE}" 2>&1 &
  MOCK_SSE_PID=$!
  echo "[cli-bridge] Mock SSE logs appended to ${LOG_FILE}"
  if [[ "${MOCK_SSE_ONLY}" == "1" ]]; then
    wait "${MOCK_SSE_PID}"
    exit 0
  fi
fi

echo "[cli-bridge] Starting FastAPI bridge on ${HOST}:${PORT} (latency=${LATENCY_MS}ms jitter=${JITTER_MS}ms drop=${DROP_RATE})"
echo "[cli-bridge] Logs -> ${LOG_FILE} (tail -f recommended)"

BREADBOARD_CLI_LATENCY_MS="${LATENCY_MS}" \
BREADBOARD_CLI_JITTER_MS="${JITTER_MS}" \
BREADBOARD_CLI_DROP_RATE="${DROP_RATE}" \
python -m agentic_coder_prototype.api.cli_bridge.server "$@" 2>&1 | tee -a "${LOG_FILE}"
