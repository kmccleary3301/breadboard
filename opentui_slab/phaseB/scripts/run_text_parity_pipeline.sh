#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKROOT="$(cd "${ROOT}/../.." && pwd)"

SMOKE_ARTIFACT_DIR="${1:-${WORKROOT}/docs_tmp/cli_phase_5/opentui_phaseb_smoke_artifacts}"
REPLAY_ARTIFACT_DIR="${2:-${WORKROOT}/docs_tmp/cli_phase_5/opentui_phaseb_replay_artifacts}"
OUT_JSON="${3:-${WORKROOT}/docs_tmp/cli_phase_5/OPENTUI_SLAB_TEXT_PARITY_MARKERS_20260219.json}"
OUT_MD="${4:-${WORKROOT}/docs_tmp/cli_phase_5/OPENTUI_SLAB_TEXT_PARITY_MARKERS_20260219.md}"
MANIFEST_JSON="${ROOT}/phaseB/parity_scenario_manifest_v1.json"

OC_STDOUT_1="${WORKROOT}/docs_tmp/cli_phase_3/_isolated_runs/opentui_phaseC/20260113-160336/worktree/breadboard_repo/misc/opencode_runs/goldens/1.0.193/phase8_async_subagents_v1/current/stdout.jsonl"
OC_STDOUT_2="${WORKROOT}/docs_tmp/cli_phase_3/_isolated_runs/opentui_phaseC/20260113-160336/worktree/breadboard_repo/misc/oh_my_opencode_runs/goldens/opencode_1.0.193__oh-my-opencode_2.5.1/phase8_async_subagents_v1_background_cancel_taskid/current/stdout.jsonl"
OC_STDOUT_3="${WORKROOT}/breadboard_repo/misc/opencode_runs/goldens/1.2.6/phaseB_artifact_output_ref_v1/current/stdout.jsonl"
OC_STDOUT_4="${WORKROOT}/breadboard_repo/misc/opencode_runs/goldens/1.2.6/phaseB_artifact_diff_ref_v1/current/stdout.jsonl"
OC_EVT_1="${WORKROOT}/docs_tmp/cli_phase_3/_isolated_runs/opentui_phaseC/20260113-160336/worktree/breadboard_repo/misc/multi_agent_runs/opencode_phase8_async_event_log.jsonl"
OC_EVT_2="${WORKROOT}/docs_tmp/cli_phase_3/_isolated_runs/opentui_phaseC/20260113-160336/worktree/breadboard_repo/misc/multi_agent_runs/oh_my_opencode_phase8_async_event_log.jsonl"

echo "[text-parity-pipeline] smoke artifacts: ${SMOKE_ARTIFACT_DIR}"
echo "[text-parity-pipeline] replay artifacts: ${REPLAY_ARTIFACT_DIR}"

cd "${ROOT}"

bash "${ROOT}/phaseB/scripts/run_text_capture_smoke_suite.sh" "${SMOKE_ARTIFACT_DIR}"
bash "${ROOT}/phaseB/scripts/replay_fixture_text_capture_suite.sh" "${REPLAY_ARTIFACT_DIR}"

"${ROOT}/phaseB/scripts/validate_text_capture_artifacts.sh" "${SMOKE_ARTIFACT_DIR}"
"${ROOT}/phaseB/scripts/validate_text_capture_artifacts.sh" "${REPLAY_ARTIFACT_DIR}"

python3 "${ROOT}/phaseB/scripts/compare_text_parity_markers.py" \
  --slab-dir "${SMOKE_ARTIFACT_DIR}" \
  --slab-dir "${REPLAY_ARTIFACT_DIR}" \
  --opencode-stdout-jsonl "${OC_STDOUT_1}" \
  --opencode-stdout-jsonl "${OC_STDOUT_2}" \
  --opencode-stdout-jsonl "${OC_STDOUT_3}" \
  --opencode-stdout-jsonl "${OC_STDOUT_4}" \
  --opencode-event-log-jsonl "${OC_EVT_1}" \
  --opencode-event-log-jsonl "${OC_EVT_2}" \
  --manifest-json "${MANIFEST_JSON}" \
  --output-json "${OUT_JSON}" \
  --output-md "${OUT_MD}"

echo "[text-parity-pipeline] pass"
