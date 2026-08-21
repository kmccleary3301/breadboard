#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRIDGE_DIR="${PASSIVE_WINDOW_SOURCE_DIRS_PRIMARY:-/shared_folders/querylake_server/ray_testing/ray_SCE/atp_passive_window_sources}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

mkdir -p "${BRIDGE_DIR}"

echo "[upstream-producer] repo=${REPO_ROOT}"
echo "[upstream-producer] bridge=${BRIDGE_DIR}"
echo "[upstream-producer] python=${PYTHON_BIN}"

"${PYTHON_BIN}" "${REPO_ROOT}/scripts/atp_ops_digest.py" \
  --repo-root "${REPO_ROOT}" \
  --out "${BRIDGE_DIR}/atp_ops_digest.latest.json"

"${PYTHON_BIN}" "${REPO_ROOT}/scripts/evolake_toy_campaign_nightly.py" \
  --repo-root "${REPO_ROOT}" \
  --out "${BRIDGE_DIR}/evolake_toy_campaign_nightly.local.json"

"${PYTHON_BIN}" "${REPO_ROOT}/scripts/validate_passive_window_source_drop.py" \
  --source-dir "${BRIDGE_DIR}" || true

touch "${BRIDGE_DIR}/.drop_complete"

echo "[upstream-producer] done"
