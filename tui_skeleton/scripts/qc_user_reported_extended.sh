#!/usr/bin/env bash
set -euo pipefail

run_with_retry() {
  local script_name="$1"
  local attempts="${2:-2}"
  local try=1
  while (( try <= attempts )); do
    if npm run -s "$script_name"; then
      return 0
    fi
    echo "[qc] ${script_name} failed (attempt ${try}/${attempts})" >&2
    if (( try < attempts )); then
      sleep 1
    fi
    try=$((try + 1))
  done
  return 1
}

run_with_retry "qc:user-reported:quick" 2
run_with_retry "qc:user-reported:slash-suggest-select-models" 2
run_with_retry "qc:user-reported:command-modal-sweep" 2
run_with_retry "qc:user-reported:modal-matrix" 2
run_with_retry "qc:user-reported:resize-stability" 2
run_with_retry "qc:user-reported:post-submit-resize" 2
run_with_retry "qc:user-reported:menu-combo-stability" 2
run_with_retry "qc:user-reported:resize-artifact-gate" 2
run_with_retry "qc:user-reported:resize-modal-churn" 2
run_with_retry "qc:user-reported:resize-submit-churn" 2
run_with_retry "qc:user-reported:landing-persistence" 2
run_with_retry "qc:user-reported:landing-dup-churn" 2
run_with_retry "qc:user-reported:disconnected-resize-churn" 2
run_with_retry "qc:user-reported:resize-history-integrity" 2
run_with_retry "qc:user-reported:resize-history-stress" 2

echo "[qc] extended user-reported checks passed"
