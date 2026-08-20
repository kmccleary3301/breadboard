#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-${REPO_ROOT}/docs_tmp/cli_phase_5}"
RUN_ROOT="${2:-${REPO_ROOT}/docs_tmp/tmux_captures/scenarios/phase4_replay}"
STAMP="${3:-$(date +%Y%m%d-%H%M%S)}"

mkdir -p "${OUT_DIR}"

OUT_JSON="${OUT_DIR}/phase5_advanced_behavior_stress_${STAMP}.json"
OUT_MD="${OUT_DIR}/phase5_advanced_behavior_stress_${STAMP}.md"

echo "[phase5-advanced-behavior] run_root=${RUN_ROOT}"
echo "[phase5-advanced-behavior] out_dir=${OUT_DIR}"
echo "[phase5-advanced-behavior] stamp=${STAMP}"

python "${REPO_ROOT}/scripts/validate_phase5_advanced_behavior_stress.py" \
  --run-root "${RUN_ROOT}" \
  --max-frame-gap-seconds 3.2 \
  --max-unchanged-streak-seconds 2.2 \
  --min-active-coverage 0.01 \
  --min-nonzero-deltas 1 \
  --fail-on-missing-scenarios \
  --output-json "${OUT_JSON}" \
  --output-md "${OUT_MD}"

echo "[phase5-advanced-behavior] pass"
echo "[phase5-advanced-behavior] outputs:"
echo "  ${OUT_JSON}"
echo "  ${OUT_MD}"
