#!/usr/bin/env bash
set -euo pipefail

if [[ "${BREADBOARD_PROOF_DIRECT_ENGINE:-0}" == "1" ]]; then
  exec /usr/local/bin/omp "$@"
fi

bun /proof/container_engine_proxy.mjs &
proxy_pid=$!
cleanup() {
  kill "$proxy_pid" 2>/dev/null || true
  wait "$proxy_pid" 2>/dev/null || true
}
trap cleanup EXIT

for _ in $(seq 1 100); do
  if curl -fsS http://127.0.0.1:9099/health >/dev/null 2>&1; then
    exec /usr/local/bin/omp "$@"
  fi
  sleep 0.1
done

echo "loopback engine proxy did not become ready" >&2
exit 70
