#!/usr/bin/env bash
set -euo pipefail

# Run Ray integration tests that require socket access.
# Execute outside the restricted sandbox environment.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

CONDA_BIN="$(command -v conda || true)"
if [[ -z "$CONDA_BIN" ]]; then
  echo "conda not found in PATH. Install conda or adjust the script to use your Python environment." >&2
  exit 1
fi

echo "[ray-tests] Running Ray-enabled session tests (requires network + sockets)."
echo "[ray-tests] Command: pytest tests/test_agent_session.py -q"

"$CONDA_BIN" run -n ray_sce_test pytest tests/test_agent_session.py -q "$@"
