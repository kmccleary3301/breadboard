#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TUI_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
P16_ROOT="${TUI_ROOT}/../docs_tmp/cli_phase_6/CODESIGN_p16/implementation_validation_p16_final_design_complete"
STAMP="${P16_PHASE_H_STAMP:-$(date +%Y%m%d-%H%M%S)}"
ARTIFACT_DIR="${P16_ROOT}/artifacts/goal_recovery/phase_h_goal_recovery_${STAMP}"

mkdir -p "${ARTIFACT_DIR}/goal_fork" "${ARTIFACT_DIR}/logs"

cd "${TUI_ROOT}"

GOAL_FORK_DIR="${ARTIFACT_DIR}/goal_fork"
BREADBOARD_STATE_DUMP_PATH="${GOAL_FORK_DIR}/state.ndjson" \
BREADBOARD_STATE_DUMP_MODE=full \
BREADBOARD_STATE_DUMP_RATE_MS=50 \
BREADBOARD_PTY_HOME="${GOAL_FORK_DIR}/home" \
BREADBOARD_ENGINE_MODE=local-owned \
BREADBOARD_API_URL="http://127.0.0.1:19331" \
BREADBOARD_ENGINE_KEEPALIVE=0 \
bash scripts/run_legacy_pty_case.sh \
  --script scripts/p16_phase_h_goal_fork_pty.json \
  --snapshots "${GOAL_FORK_DIR}/snapshots.txt" \
  --raw-output "${GOAL_FORK_DIR}/raw_output.txt" \
  --cmd "node dist/main.js repl --tui classic --workspace ${GOAL_FORK_DIR}/dummy_workspace" \
  --cols 120 \
  --rows 36 \
  --watchdog-ms 30000 \
  --max-duration-ms 120000 \
  >"${GOAL_FORK_DIR}/harness_output.txt" 2>&1
node --import tsx scripts/p16_phase_h_goal_fork_gate.ts "${GOAL_FORK_DIR}" 0 >"${ARTIFACT_DIR}/logs/goal_fork_gate.log" 2>&1

V7_ARTIFACT_DIR="${ARTIFACT_DIR}/doctor_footer" \
V7_DOCTOR_FOOTER_PORT=19332 \
bash scripts/qc_v7_doctor_footer_pty.sh >"${ARTIFACT_DIR}/logs/doctor_footer.log" 2>&1

V7_ARTIFACT_DIR="${ARTIFACT_DIR}/retry_after_unknown" \
V7_RETRY_AFTER_UNKNOWN_PORT=19333 \
bash scripts/qc_v7_retry_after_unknown_pty.sh >"${ARTIFACT_DIR}/logs/retry_after_unknown.log" 2>&1

V7_ARTIFACT_DIR="${ARTIFACT_DIR}/resume_after_recovery" \
V7_RESUME_AFTER_RECOVERY_PORT=19334 \
bash scripts/qc_v7_resume_after_recovery_pty.sh >"${ARTIFACT_DIR}/logs/resume_after_recovery.log" 2>&1

V7_ARTIFACT_DIR="${ARTIFACT_DIR}/stop_during_recovery" \
V7_STOP_DURING_RECOVERY_PORT=19335 \
bash scripts/qc_v7_stop_during_recovery_pty.sh >"${ARTIFACT_DIR}/logs/stop_during_recovery.log" 2>&1

V7_ARTIFACT_DIR="${ARTIFACT_DIR}/resize_during_reconnect" \
V7_RESIZE_DURING_RECONNECT_PORT=19336 \
bash scripts/qc_v7_resize_during_reconnect_pty.sh >"${ARTIFACT_DIR}/logs/resize_during_reconnect.log" 2>&1

P16_PHASE_H_ARTIFACT_DIR="${ARTIFACT_DIR}" pnpm exec tsx scripts/p16_phase_h_goal_recovery_evidence.ts

echo "P16 Phase H goal/recovery bundle passed: ${ARTIFACT_DIR}"
