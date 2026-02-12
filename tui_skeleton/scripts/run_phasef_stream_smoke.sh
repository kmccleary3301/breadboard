#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TOP_DIR="$(cd "${ROOT_DIR}/.." && pwd)"
TOP_DIR="$(cd "${ROOT_DIR}/.." && pwd)"

timestamp="$(date -u +"%Y%m%d-%H%M%S")"
OUT_DIR="${1:-${TOP_DIR}/docs_tmp/cli_phase_3/_isolated_runs/phasef_stream_smoke_${timestamp}}"
LOG_DIR="${OUT_DIR}/logs"

mkdir -p "${OUT_DIR}" "${LOG_DIR}"

MOCK_HOST="127.0.0.1"
MOCK_PORT="${BREADBOARD_CLI_MOCK_SSE_PORT:-9191}"
MOCK_LOG="${LOG_DIR}/mock_sse.log"
MOCK_SCRIPT="${BREADBOARD_MOCK_SSE_SCRIPT:-${ROOT_DIR}/tui_skeleton/scripts/mock_sse_canonical_stream.json}"
INK_SCRIPT="${BREADBOARD_INK_SCRIPT:-${ROOT_DIR}/tui_skeleton/scripts/canonical_stream_script.json}"

cleanup() {
  if [[ -n "${MOCK_PID:-}" ]]; then
    kill "${MOCK_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "[phasef] output: ${OUT_DIR}"

npm -C "${ROOT_DIR}/tui_skeleton" run build >/dev/null

echo "[phasef] starting mock SSE on ${MOCK_HOST}:${MOCK_PORT}"
tsx "${ROOT_DIR}/tui_skeleton/tools/mock/mockSseServer.ts" \
  --script "${MOCK_SCRIPT}" \
  --host "${MOCK_HOST}" \
  --port "${MOCK_PORT}" \
  >"${MOCK_LOG}" 2>&1 &
MOCK_PID=$!
sleep 0.3

echo "[phasef] ink script capture"
BREADBOARD_API_URL="http://${MOCK_HOST}:${MOCK_PORT}" \
  node "${ROOT_DIR}/tui_skeleton/dist/main.js" repl --remote-stream \
  --script "${INK_SCRIPT}" \
  --script-output "${OUT_DIR}/ink_script_snapshot.txt"

echo "[phasef] opentui capture"
(
  cd "${ROOT_DIR}/opentui_slab"
  set +e
  OPENTUI_CAPTURE_PATH="${OUT_DIR}/opentui_ui_capture.txt"
  OPENTUI_ANSI_CAPTURE="${OUT_DIR}/opentui_ui_capture.ansi"
  OPENTUI_PNG_CAPTURE="${OUT_DIR}/opentui_ui_capture.png"
  CAPTURE_COLS="${BREADBOARD_UI_CAPTURE_COLS:-100}"
  CAPTURE_ROWS="${BREADBOARD_UI_CAPTURE_ROWS:-30}"
  OPENTUI_ENV="BREADBOARD_API_URL=http://${MOCK_HOST}:${MOCK_PORT} \
BREADBOARD_UI_CAPTURE_PATH=${OPENTUI_CAPTURE_PATH} \
BREADBOARD_UI_CAPTURE_ON_EVENT=run.end \
BREADBOARD_UI_CAPTURE_DELAY_MS=200 \
BREADBOARD_UI_RAW_STREAM=${BREADBOARD_UI_RAW_STREAM:-0} \
BREADBOARD_UI_REASONING=${BREADBOARD_UI_REASONING:-0} \
BREADBOARD_UI_TOOL_INLINE=1 \
BREADBOARD_UI_FORCE_COLS=${CAPTURE_COLS} \
BREADBOARD_UI_FORCE_ROWS=${CAPTURE_ROWS} \
COLUMNS=${CAPTURE_COLS} \
LINES=${CAPTURE_ROWS}"
  timeout -k 2s 20s script -q -c "${OPENTUI_ENV} bun run \"phaseB/controller.ts\" --external-bridge --base-url \"http://${MOCK_HOST}:${MOCK_PORT}\" --task \"start\" --exit-after-ms 12000" "${OPENTUI_ANSI_CAPTURE}" >/dev/null 2>&1
  if [[ -s "${OPENTUI_ANSI_CAPTURE}" ]]; then
    python - <<PY
import re
from pathlib import Path
path = Path("${OPENTUI_ANSI_CAPTURE}")
payload = path.read_text(errors="ignore")
payload = re.sub(r"^Script (started|done).*?\\r?\\n", "", payload, flags=re.MULTILINE)
path.write_text(payload)
PY
    python "${TOP_DIR}/scripts/tmux_capture_to_png.py" \
      --ansi "${OPENTUI_ANSI_CAPTURE}" \
      --cols "${CAPTURE_COLS}" \
      --rows "${CAPTURE_ROWS}" \
      --out "${OPENTUI_PNG_CAPTURE}" \
      --bg "0f172a" \
      --fg "f1f5f9" \
      --font-size 18 \
      --scale 1.0
  fi
  set -e
)

if [[ ! -s "${OUT_DIR}/ink_script_snapshot.txt" ]]; then
  echo "[phasef] missing ink_script_snapshot.txt" >&2
  exit 1
fi
if [[ ! -s "${OUT_DIR}/opentui_ui_capture.txt" ]]; then
  echo "[phasef] missing opentui_ui_capture.txt" >&2
  exit 1
fi
if [[ ! -s "${OUT_DIR}/opentui_ui_capture.png" ]]; then
  echo "[phasef] missing opentui_ui_capture.png" >&2
  exit 1
fi

echo "[phasef] ok"
