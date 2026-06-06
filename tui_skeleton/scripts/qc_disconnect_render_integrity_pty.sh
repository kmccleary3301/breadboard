#!/usr/bin/env bash
set -euo pipefail

echo "[qc][legacy] $(basename "$0") is a deterministic user-reported disconnect render lane." >&2
echo "[qc][legacy] It verifies preserved scrollback does not absorb transient network banners or duplicate user echoes." >&2

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
MOCK_HOST="127.0.0.1"
MOCK_PORT="${BREADBOARD_CLI_MOCK_SSE_PORT:-9564}"
MOCK_SCRIPT="${ROOT_DIR}/scripts/mock_sse_disconnect_render_integrity.json"
SNAPSHOT_PATH="${BREADBOARD_DISCONNECT_RENDER_SNAPSHOT:-${ROOT_DIR}/scripts/_tmp_qc_disconnect_render_integrity_pty.txt}"
STATE_DUMP_PATH="${BREADBOARD_DISCONNECT_RENDER_STATE:-${ROOT_DIR}/scripts/_tmp_qc_disconnect_render_integrity_state.ndjson}"
MOCK_LOG="${ROOT_DIR}/scripts/_tmp_qc_disconnect_render_integrity_mock.log"

cleanup() {
  if [[ -n "${MOCK_PID:-}" ]]; then
    kill "${MOCK_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${KILLER_PID:-}" ]]; then
    kill "${KILLER_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

rm -f "${SNAPSHOT_PATH}" "${STATE_DUMP_PATH}" "${MOCK_LOG}"

cd "${ROOT_DIR}"
pnpm exec tsx tools/mock/mockSseServer.ts \
  --script "${MOCK_SCRIPT}" \
  --host "${MOCK_HOST}" \
  --port "${MOCK_PORT}" \
  >"${MOCK_LOG}" 2>&1 &
MOCK_PID=$!

sleep 1
( sleep 3.2; kill "${MOCK_PID}" >/dev/null 2>&1 || true ) &
KILLER_PID=$!

BREADBOARD_ENGINE_MODE="external" \
BREADBOARD_API_URL="http://${MOCK_HOST}:${MOCK_PORT}" \
BREADBOARD_STATE_DUMP_PATH="${STATE_DUMP_PATH}" \
BREADBOARD_STATE_DUMP_MODE="ndjson" \
BREADBOARD_STATE_DUMP_RATE_MS="0" \
BREADBOARD_STREAM_MAX_RETRIES="5" \
BREADBOARD_STREAM_STALL_TIMEOUT_MS="1500" \
node --import tsx scripts/repl_pty_harness.ts \
  --script scripts/qc_cases/disconnect_render_integrity.json \
  --snapshots "${SNAPSHOT_PATH}" \
  --max-duration-ms 90000

node --import tsx scripts/qc_disconnect_render_integrity_gate.ts "${SNAPSHOT_PATH}" "${STATE_DUMP_PATH}"
