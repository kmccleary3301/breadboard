#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
IPC_LOG="${TMP_DIR}/ipc.log"
PANE_LOG="${TMP_DIR}/pane.log"
SESSION="bb_opentui_artifact_render_$RANDOM"
CONTROLLER_PID=""
ARTIFACT_DIR="${BREADBOARD_OPENTUI_SMOKE_ARTIFACT_DIR:-}"
ARTIFACT_STAMP="$(date -u +%Y%m%d-%H%M%S)"

persist_artifacts() {
  if [[ -z "${ARTIFACT_DIR}" ]]; then
    return
  fi
  mkdir -p "${ARTIFACT_DIR}"
  cp "${PANE_LOG}" "${ARTIFACT_DIR}/artifact_render_${ARTIFACT_STAMP}_pane.txt" 2>/dev/null || true
  cp "${IPC_LOG}" "${ARTIFACT_DIR}/artifact_render_${ARTIFACT_STAMP}_ipc.log" 2>/dev/null || true
}

cleanup() {
  set +e
  persist_artifacts
  if [[ -n "${SESSION:-}" ]]; then
    tmux has-session -t "${SESSION}" 2>/dev/null && tmux kill-session -t "${SESSION}"
  fi
  if [[ -n "${CONTROLLER_PID:-}" ]]; then
    kill "${CONTROLLER_PID}" 2>/dev/null || true
  fi
  rm -rf "${TMP_DIR}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

cd "${ROOT}"

bun run phaseB/scripts/mock_artifact_render_controller.ts 2> "${IPC_LOG}" &
CONTROLLER_PID="$!"

HOST=""
PORT=""
for _ in $(seq 1 50); do
  if grep -q "IPC_HOST=" "${IPC_LOG}" 2>/dev/null; then
    HOST="$(sed -n 's/.*IPC_HOST=\([^ ]*\).*/\1/p' "${IPC_LOG}" | tail -n1)"
    PORT="$(sed -n 's/.*IPC_PORT=\([0-9]*\).*/\1/p' "${IPC_LOG}" | tail -n1)"
    break
  fi
  sleep 0.1
done

if [[ -z "${HOST}" || -z "${PORT}" ]]; then
  echo "[artifact-render-smoke] failed: could not parse IPC host/port"
  cat "${IPC_LOG}" || true
  exit 1
fi

tmux new-session -d -x 220 -y 45 -s "${SESSION}" \
  "cd '${ROOT}' && BREADBOARD_IPC_HOST='${HOST}' BREADBOARD_IPC_PORT='${PORT}' bun run phaseB/ui.ts"

FOUND_OUTPUT_ARTIFACT=0
FOUND_DIFF_ARTIFACT=0

for _ in $(seq 1 90); do
  if ! tmux has-session -t "${SESSION}" 2>/dev/null; then
    break
  fi
  tmux capture-pane -pt "${SESSION}:0.0" -S -220 > "${PANE_LOG}" || true
  grep -Eq "artifact-sample-output.txt" "${PANE_LOG}" && FOUND_OUTPUT_ARTIFACT=1 || true
  grep -Eq "artifact-sample-diff.diff" "${PANE_LOG}" && FOUND_DIFF_ARTIFACT=1 || true
  if [[ "${FOUND_OUTPUT_ARTIFACT}" -eq 1 && "${FOUND_DIFF_ARTIFACT}" -eq 1 ]]; then
    break
  fi
  sleep 0.1
done

if [[ "${FOUND_OUTPUT_ARTIFACT}" -ne 1 ]]; then
  echo "[artifact-render-smoke] failed: output artifact line missing"
  echo "--- pane ---"
  cat "${PANE_LOG}" || true
  echo "--- ipc ---"
  cat "${IPC_LOG}" || true
  exit 1
fi

if [[ "${FOUND_DIFF_ARTIFACT}" -ne 1 ]]; then
  echo "[artifact-render-smoke] failed: diff artifact line missing"
  echo "--- pane ---"
  cat "${PANE_LOG}" || true
  echo "--- ipc ---"
  cat "${IPC_LOG}" || true
  exit 1
fi

if ! grep -Eq "Inline output truncated to artifact reference." "${PANE_LOG}"; then
  echo "[artifact-render-smoke] failed: output artifact note missing"
  echo "--- pane ---"
  cat "${PANE_LOG}" || true
  exit 1
fi

if ! grep -Eq "Large unified diff exported to artifact." "${PANE_LOG}"; then
  echo "[artifact-render-smoke] failed: diff artifact note missing"
  echo "--- pane ---"
  cat "${PANE_LOG}" || true
  exit 1
fi

echo "[artifact-render-smoke] pass"
