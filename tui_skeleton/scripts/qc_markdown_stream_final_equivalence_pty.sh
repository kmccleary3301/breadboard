#!/usr/bin/env bash
set -euo pipefail

echo "[qc][legacy] $(basename "$0") is a compatibility or narrow-repro lane." >&2
echo "[qc][legacy] Prefer scripts/qc_profile_matrix.sh and qc:profile:* for canonical QC." >&2

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
MOCK_HOST="127.0.0.1"
STREAM_PORT="${BREADBOARD_STREAM_EQUIV_PORT:-9502}"
FINAL_PORT="${BREADBOARD_FINAL_EQUIV_PORT:-9503}"
STREAM_SNAPSHOT="${BREADBOARD_STREAM_EQUIV_SNAPSHOT:-${ROOT_DIR}/scripts/_tmp_qc_markdown_equivalence_stream.txt}"
FINAL_SNAPSHOT="${BREADBOARD_FINAL_EQUIV_SNAPSHOT:-${ROOT_DIR}/scripts/_tmp_qc_markdown_equivalence_final.txt}"
STREAM_LOG="${ROOT_DIR}/scripts/_tmp_qc_markdown_equivalence_stream_mock.log"
FINAL_LOG="${ROOT_DIR}/scripts/_tmp_qc_markdown_equivalence_final_mock.log"

cleanup() {
  if [[ -n "${STREAM_PID:-}" ]]; then kill "${STREAM_PID}" >/dev/null 2>&1 || true; fi
  if [[ -n "${FINAL_PID:-}" ]]; then kill "${FINAL_PID}" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

rm -f "${STREAM_SNAPSHOT}" "${FINAL_SNAPSHOT}" "${STREAM_LOG}" "${FINAL_LOG}"

pnpm exec tsx "${ROOT_DIR}/tools/mock/mockSseServer.ts" \
  --script "${ROOT_DIR}/scripts/mock_sse_markdown_equivalence_stream.json" \
  --host "${MOCK_HOST}" \
  --port "${STREAM_PORT}" \
  >"${STREAM_LOG}" 2>&1 &
STREAM_PID=$!
sleep 1
BREADBOARD_ENGINE_MODE="external" \
BREADBOARD_API_URL="http://${MOCK_HOST}:${STREAM_PORT}" \
node --import tsx "${ROOT_DIR}/scripts/repl_pty_harness.ts" \
  --script "${ROOT_DIR}/scripts/markdown_equivalence_pty.json" \
  --snapshots "${STREAM_SNAPSHOT}" \
  --max-duration-ms 120000
kill "${STREAM_PID}" >/dev/null 2>&1 || true
unset STREAM_PID

pnpm exec tsx "${ROOT_DIR}/tools/mock/mockSseServer.ts" \
  --script "${ROOT_DIR}/scripts/mock_sse_markdown_equivalence_final.json" \
  --host "${MOCK_HOST}" \
  --port "${FINAL_PORT}" \
  >"${FINAL_LOG}" 2>&1 &
FINAL_PID=$!
sleep 1
BREADBOARD_ENGINE_MODE="external" \
BREADBOARD_API_URL="http://${MOCK_HOST}:${FINAL_PORT}" \
node --import tsx "${ROOT_DIR}/scripts/repl_pty_harness.ts" \
  --script "${ROOT_DIR}/scripts/markdown_equivalence_pty.json" \
  --snapshots "${FINAL_SNAPSHOT}" \
  --max-duration-ms 120000

node --import tsx "${ROOT_DIR}/scripts/qc_markdown_stream_final_equivalence_gate.ts" "${STREAM_SNAPSHOT}" "${FINAL_SNAPSHOT}"
