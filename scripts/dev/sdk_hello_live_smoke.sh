#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_TS=1
SERVER_PID=""
SERVER_LOG=""

usage() {
  cat <<'EOF'
Usage: scripts/dev/sdk_hello_live_smoke.sh [--no-ts]

Start a local engine unless BREADBOARD_BASE_URL is set, then run the public
Python and TypeScript SDK health examples against that same base URL.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-ts)
      RUN_TS=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[sdk-live] unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -n "${BREADBOARD_PYTHON:-}" ]]; then
  PYTHON_BIN="${BREADBOARD_PYTHON}"
elif [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi
NODE_BIN="${NODE:-node}"

cleanup() {
  if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
  if [[ -n "${SERVER_LOG}" ]]; then
    rm -f "${SERVER_LOG}"
  fi
}
trap cleanup EXIT

if [[ -n "${BREADBOARD_BASE_URL:-}" ]]; then
  BASE_URL="${BREADBOARD_BASE_URL}"
  echo "[sdk-live] using configured server ${BASE_URL}"
else
  PORT="${BREADBOARD_CLI_PORT:-$("${PYTHON_BIN}" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')}"
  BASE_URL="http://127.0.0.1:${PORT}"
  SERVER_LOG="$(mktemp "${TMPDIR:-/tmp}/breadboard-sdk-live.XXXXXX.log")"
  echo "[sdk-live] starting engine at ${BASE_URL}"
  (
    cd "${ROOT_DIR}"
    BREADBOARD_CLI_HOST=127.0.0.1 BREADBOARD_CLI_PORT="${PORT}" \
      "${PYTHON_BIN}" -m breadboard_engine.api.cli_bridge.server
  ) >"${SERVER_LOG}" 2>&1 &
  SERVER_PID=$!
fi

health_args=(--fail --silent --show-error)
if [[ -n "${BREADBOARD_API_TOKEN:-}" ]]; then
  health_args+=(--header "Authorization: Bearer ${BREADBOARD_API_TOKEN}")
fi

ready=0
for _ in {1..120}; do
  if curl "${health_args[@]}" "${BASE_URL%/}/v1/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  if [[ -n "${SERVER_PID}" ]] && ! kill -0 "${SERVER_PID}" 2>/dev/null; then
    echo "[sdk-live] engine exited before becoming ready" >&2
    cat "${SERVER_LOG}" >&2
    exit 1
  fi
  sleep 0.25
done
if [[ "${ready}" != "1" ]]; then
  echo "[sdk-live] engine did not become ready at ${BASE_URL}" >&2
  if [[ -n "${SERVER_LOG}" ]]; then
    cat "${SERVER_LOG}" >&2
  fi
  exit 1
fi

export BREADBOARD_BASE_URL="${BASE_URL}"
"${PYTHON_BIN}" "${ROOT_DIR}/scripts/dev/python_sdk_hello.py"
if [[ "${RUN_TS}" == "1" ]]; then
  "${NODE_BIN}" "${ROOT_DIR}/scripts/dev/ts_sdk_hello.mjs"
fi

echo "[sdk-live] ok"
