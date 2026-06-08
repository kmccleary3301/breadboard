#!/usr/bin/env bash
set -euo pipefail

echo "[qc][legacy] $(basename "$0") is a compatibility or narrow-repro lane." >&2
echo "[qc][legacy] Prefer scripts/qc_profile_matrix.sh and qc:profile:* for canonical QC." >&2

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

MOCK_HOST="127.0.0.1"
MOCK_PORT="${BREADBOARD_CLI_MOCK_SSE_PORT:-9501}"
MOCK_SCRIPT="${BREADBOARD_MOCK_SSE_SCRIPT:-${ROOT_DIR}/scripts/mock_sse_two_turn_long_markdown.json}"
SNAPSHOT_PATH="${BREADBOARD_TWO_TURN_MARKDOWN_SNAPSHOT:-${ROOT_DIR}/scripts/_tmp_qc_two_turn_long_markdown.txt}"
STATE_PATH="${BREADBOARD_TWO_TURN_MARKDOWN_STATE:-${ROOT_DIR}/scripts/_tmp_qc_two_turn_long_markdown_state.jsonl}"
MOCK_LOG="${ROOT_DIR}/scripts/_tmp_qc_two_turn_long_markdown_mock.log"

cleanup() {
  if [[ -n "${MOCK_PID:-}" ]]; then
    kill "${MOCK_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

rm -f "${SNAPSHOT_PATH}" "${STATE_PATH}" "${MOCK_LOG}"

pnpm exec tsx "${ROOT_DIR}/tools/mock/mockSseServer.ts" \
  --script "${MOCK_SCRIPT}" \
  --host "${MOCK_HOST}" \
  --port "${MOCK_PORT}" \
  >"${MOCK_LOG}" 2>&1 &
MOCK_PID=$!

sleep 1

BREADBOARD_ENGINE_MODE="external" \
BREADBOARD_API_URL="http://${MOCK_HOST}:${MOCK_PORT}" \
BREADBOARD_STATE_DUMP_PATH="${STATE_PATH}" \
BREADBOARD_STATE_DUMP_MODE="full" \
node --import tsx "${ROOT_DIR}/scripts/repl_pty_harness.ts" \
  --script "${ROOT_DIR}/scripts/two_turn_long_markdown_pty.json" \
  --snapshots "${SNAPSHOT_PATH}" \
  --rows 28 \
  --cols 116 \
  --max-duration-ms 180000

node --import tsx "${ROOT_DIR}/scripts/qc_two_turn_long_markdown_gate.ts" "${SNAPSHOT_PATH}" "${STATE_PATH}"
