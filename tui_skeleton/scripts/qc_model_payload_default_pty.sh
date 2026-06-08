#!/usr/bin/env bash
set -euo pipefail

echo "[qc][legacy] $(basename "$0") is a compatibility or narrow-repro lane." >&2
echo "[qc][legacy] Prefer scripts/qc_profile_matrix.sh and qc:profile:* for canonical QC." >&2

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
MOCK_HOST="127.0.0.1"
MOCK_PORT="${BREADBOARD_MODEL_PAYLOAD_PORT:-9504}"
SNAPSHOT_PATH="${BREADBOARD_MODEL_PAYLOAD_SNAPSHOT:-${ROOT_DIR}/scripts/_tmp_qc_model_payload_default.txt}"
RECORD_PATH="${BREADBOARD_MODEL_PAYLOAD_RECORD:-${ROOT_DIR}/scripts/_tmp_qc_model_payload_default_records.jsonl}"
MOCK_LOG="${ROOT_DIR}/scripts/_tmp_qc_model_payload_default_mock.log"

cleanup() {
  if [[ -n "${MOCK_PID:-}" ]]; then
    kill "${MOCK_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

rm -f "${SNAPSHOT_PATH}" "${RECORD_PATH}" "${MOCK_LOG}"

BREADBOARD_MOCK_RECORD_PATH="${RECORD_PATH}" \
pnpm exec tsx "${ROOT_DIR}/tools/mock/mockSseServer.ts" \
  --script "${ROOT_DIR}/scripts/mock_sse_sample.json" \
  --host "${MOCK_HOST}" \
  --port "${MOCK_PORT}" \
  >"${MOCK_LOG}" 2>&1 &
MOCK_PID=$!

sleep 1

BREADBOARD_ENGINE_MODE="external" \
BREADBOARD_API_URL="http://${MOCK_HOST}:${MOCK_PORT}" \
node --import tsx "${ROOT_DIR}/scripts/repl_pty_harness.ts" \
  --script "${ROOT_DIR}/scripts/qc_cases/startup_landing.json" \
  --snapshots "${SNAPSHOT_PATH}" \
  --max-duration-ms 120000

node --import tsx "${ROOT_DIR}/scripts/qc_model_payload_default_gate.ts" "${RECORD_PATH}" "${SNAPSHOT_PATH}"
