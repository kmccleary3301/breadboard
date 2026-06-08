#!/usr/bin/env bash
set -euo pipefail

echo "[qc][legacy] $(basename "$0") is a compatibility or narrow-repro lane." >&2
echo "[qc][legacy] Prefer scripts/qc_profile_matrix.sh and qc:profile:* for canonical QC." >&2

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

MOCK_HOST="127.0.0.1"
MOCK_PORT="${BREADBOARD_CLI_MOCK_SSE_PORT:-9492}"
MOCK_SCRIPT="${BREADBOARD_MOCK_SSE_SCRIPT:-${ROOT_DIR}/scripts/mock_sse_tool_projection_contract.json}"
SNAPSHOT_PATH="${BREADBOARD_TOOL_PROJECTION_SNAPSHOT:-${ROOT_DIR}/scripts/_tmp_qc_tool_projection_pty.txt}"
MOCK_LOG="${ROOT_DIR}/scripts/_tmp_qc_tool_projection_mock.log"

cleanup() {
  if [[ -n "${MOCK_PID:-}" ]]; then
    kill "${MOCK_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

rm -f "${SNAPSHOT_PATH}" "${MOCK_LOG}"

pnpm exec tsx "${ROOT_DIR}/tools/mock/mockSseServer.ts" \
  --script "${MOCK_SCRIPT}" \
  --host "${MOCK_HOST}" \
  --port "${MOCK_PORT}" \
  >"${MOCK_LOG}" 2>&1 &
MOCK_PID=$!

sleep 1

BREADBOARD_ENGINE_MODE="external" \
BREADBOARD_API_URL="http://${MOCK_HOST}:${MOCK_PORT}" \
node --import tsx "${ROOT_DIR}/scripts/repl_pty_harness.ts" \
  --script "${ROOT_DIR}/scripts/tool_projection_pty.json" \
  --snapshots "${SNAPSHOT_PATH}" \
  --max-duration-ms 120000

node --import tsx "${ROOT_DIR}/scripts/qc_tool_projection_gate.ts" "${SNAPSHOT_PATH}"
