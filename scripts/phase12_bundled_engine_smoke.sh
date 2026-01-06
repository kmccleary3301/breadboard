#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

ENGINE_VERSION="${ENGINE_VERSION:-${BREADBOARD_ENGINE_VERSION:-local-dev}}"
MANIFEST_PATH="${MANIFEST_PATH:-${ROOT_DIR}/local_engine_bundles/dist/manifest.json}"

export BREADBOARD_ENGINE_AUTO_DOWNLOAD=1
export BREADBOARD_ENGINE_MANIFEST_URL="${MANIFEST_PATH}"
export BREADBOARD_ENGINE_VERSION="${ENGINE_VERSION}"
export BREADBOARD_ENGINE_KEEPALIVE="${BREADBOARD_ENGINE_KEEPALIVE:-0}"
export DOCTOR_TIMEOUT_S="${DOCTOR_TIMEOUT_S:-240}"
export RUN_TIMEOUT_S="${RUN_TIMEOUT_S:-240}"

if [[ ! -f "${MANIFEST_PATH}" ]]; then
  echo "[bundled-smoke] manifest not found at ${MANIFEST_PATH}; building local bundle (${ENGINE_VERSION})"
  python scripts/build_local_engine_bundle.py --version "${ENGINE_VERSION}"
fi

echo "[bundled-smoke] running Phase 12 live smoke using bundled engine (${ENGINE_VERSION})"
scripts/phase12_live_smoke.sh

echo "[bundled-smoke] ok"
