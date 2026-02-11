#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

MANIFEST_PATH="${ROOT_DIR}/../misc/tui_goldens/manifests/claude_compare_manifest.yaml"
OUT_ROOT="${ROOT_DIR}/../misc/tui_goldens/claude_compare"

node --import tsx "${SCRIPT_DIR}/run_tui_goldens.ts" \
  --manifest "${MANIFEST_PATH}" \
  --out "${OUT_ROOT}"

RUN_DIR="$(ls -td "${OUT_ROOT}"/run-* 2>/dev/null | head -n 1 || true)"
if [[ -z "${RUN_DIR}" ]]; then
  echo "[claude-compare] No run directory found under ${OUT_ROOT}" >&2
  exit 1
fi

node --import tsx "${SCRIPT_DIR}/compare_claude_alignment.ts" \
  --candidate-root "${RUN_DIR}" \
  --refs-root "${ROOT_DIR}/../misc/tui_goldens/claude_refs" \
  --summary

echo "[claude-compare] artifacts: ${RUN_DIR}"
