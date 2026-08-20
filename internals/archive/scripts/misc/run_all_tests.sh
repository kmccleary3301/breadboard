#!/usr/bin/env bash
set -euo pipefail

# Usage: scripts/run_all_tests.sh [--sudo]
# - Loads .env, shows progress, runs tests in ray_sce_test with xdist parallel

USE_SUDO=""
if [[ "${1:-}" == "--sudo" ]]; then
  USE_SUDO="sudo -E"
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# Resolve absolute path to conda binary so sudo doesn't lose it
CONDA_BIN="$(command -v conda || true)"
if [[ -z "$CONDA_BIN" ]]; then
  echo "conda not found in PATH. Ensure conda is installed and available before running tests." >&2
  exit 1
fi

# Ensure pytest plugins are available (sugar for progress, xdist for parallel)
"$CONDA_BIN" run -n ray_sce_test python - <<'PY'
import sys, subprocess
pkgs = ["pytest-sugar", "pytest-xdist"]
for p in pkgs:
    try:
        __import__(p.replace('-', '_'))
    except Exception:
        subprocess.check_call([sys.executable, "-m", "pip", "install", p])
PY

# Optional: show docker version if present (helps diagnose skips)
if command -v docker >/dev/null 2>&1; then
  $USE_SUDO docker version || true
fi

# Run tests with progress and durations (keep env via sudo -E; use absolute conda path)
${USE_SUDO} "$CONDA_BIN" run -n ray_sce_test pytest -n auto -vv -s -rA --durations=20


