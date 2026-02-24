#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-${REPO_ROOT}/docs_tmp/cli_phase_5}"
RUN_ROOT="${2:-${REPO_ROOT}/docs_tmp/tmux_captures/scenarios/phase4_replay}"
STAMP="${3:-$(date +%Y%m%d-%H%M%S)}"
CONTRACT_FILE="${REPO_ROOT}/config/text_contracts/phase4_text_contract_v1.json"

mkdir -p "${OUT_DIR}"

HARD_TEXT_JSON="${OUT_DIR}/phase5_replay_contract_hard_gate_${STAMP}.json"
NIGHTLY_TEXT_JSON="${OUT_DIR}/phase5_replay_contract_nightly_${STAMP}.json"
ALL_REL_JSON="${OUT_DIR}/phase5_replay_reliability_all_${STAMP}.json"
ALL_REL_MD="${OUT_DIR}/phase5_replay_reliability_all_${STAMP}.md"
HARD_REL_JSON="${OUT_DIR}/phase5_replay_reliability_hard_gate_${STAMP}.json"
HARD_REL_MD="${OUT_DIR}/phase5_replay_reliability_hard_gate_${STAMP}.md"
NIGHTLY_REL_JSON="${OUT_DIR}/phase5_replay_reliability_nightly_${STAMP}.json"
NIGHTLY_REL_MD="${OUT_DIR}/phase5_replay_reliability_nightly_${STAMP}.md"

echo "[phase5-replay-bundle] run_root=${RUN_ROOT}"
echo "[phase5-replay-bundle] out_dir=${OUT_DIR}"
echo "[phase5-replay-bundle] stamp=${STAMP}"

python "${REPO_ROOT}/scripts/validate_phase4_text_contract_suite.py" \
  --run-root "${RUN_ROOT}" \
  --scenario-set hard_gate \
  --strict \
  --fail-on-unmapped \
  --output-json "${HARD_TEXT_JSON}"

python "${REPO_ROOT}/scripts/validate_phase4_text_contract_suite.py" \
  --run-root "${RUN_ROOT}" \
  --scenario-set nightly \
  --strict \
  --fail-on-unmapped \
  --output-json "${NIGHTLY_TEXT_JSON}"

python "${REPO_ROOT}/scripts/validate_phase5_replay_reliability.py" \
  --run-root "${RUN_ROOT}" \
  --scenario-set hard_gate \
  --contract-file "${CONTRACT_FILE}" \
  --expected-render-profile phase4_locked_v5 \
  --fail-on-missing-scenarios \
  --output-json "${HARD_REL_JSON}" \
  --output-md "${HARD_REL_MD}"

python "${REPO_ROOT}/scripts/validate_phase5_replay_reliability.py" \
  --run-root "${RUN_ROOT}" \
  --scenario-set nightly \
  --contract-file "${CONTRACT_FILE}" \
  --expected-render-profile phase4_locked_v5 \
  --fail-on-missing-scenarios \
  --output-json "${NIGHTLY_REL_JSON}" \
  --output-md "${NIGHTLY_REL_MD}"

python "${REPO_ROOT}/scripts/validate_phase5_replay_reliability.py" \
  --run-root "${RUN_ROOT}" \
  --scenario-set all \
  --contract-file "${CONTRACT_FILE}" \
  --expected-render-profile phase4_locked_v5 \
  --fail-on-missing-scenarios \
  --output-json "${ALL_REL_JSON}" \
  --output-md "${ALL_REL_MD}"

echo "[phase5-replay-bundle] pass"
echo "[phase5-replay-bundle] outputs:"
echo "  ${HARD_TEXT_JSON}"
echo "  ${NIGHTLY_TEXT_JSON}"
echo "  ${HARD_REL_JSON}"
echo "  ${NIGHTLY_REL_JSON}"
echo "  ${ALL_REL_JSON}"
