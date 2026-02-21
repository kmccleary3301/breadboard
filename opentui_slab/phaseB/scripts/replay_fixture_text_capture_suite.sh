#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACT_DIR="${1:-$ROOT/../../docs_tmp/cli_phase_5/opentui_phaseb_replay_artifacts}"
mkdir -p "${ARTIFACT_DIR}"

CONFIG_PATH="${BREADBOARD_REPLAY_CONFIG_PATH:-agent_configs/opencode_openrouter_grok4fast_cli_default.yaml}"

run_fixture() {
  local fixture_id="$1"
  local replay_path="$2"
  shift 2
  local required_tokens=("$@")

  local tmp_dir
  tmp_dir="$(mktemp -d)"
  local ipc_log="${tmp_dir}/controller_ipc.log"
  local controller_log="${tmp_dir}/controller.log"
  local pane_log="${tmp_dir}/pane.log"
  local session="bb_opentui_replay_${fixture_id}_$RANDOM"
  local controller_pid=""
  local stamp
  stamp="$(date -u +%Y%m%d-%H%M%S)"

  cleanup_fixture() {
    set +e
    if [[ -n "${session:-}" ]]; then
      tmux has-session -t "${session}" 2>/dev/null && tmux kill-session -t "${session}"
    fi
    if [[ -n "${controller_pid:-}" ]]; then
      kill "${controller_pid}" 2>/dev/null || true
    fi
    cp "${pane_log}" "${ARTIFACT_DIR}/${fixture_id}_${stamp}_pane.txt" 2>/dev/null || true
    cp "${controller_log}" "${ARTIFACT_DIR}/${fixture_id}_${stamp}_controller.log" 2>/dev/null || true
    rm -rf "${tmp_dir}" >/dev/null 2>&1 || true
  }

  cd "${ROOT}"
  bun run phaseB/controller.ts \
    --no-ui \
    --print-ipc \
    --exit-after-ms 70000 \
    --config "${CONFIG_PATH}" \
    >"${controller_log}" 2>"${ipc_log}" &
  controller_pid="$!"

  local host=""
  local port=""
  for _ in $(seq 1 180); do
    if grep -q '"ipc"' "${ipc_log}" 2>/dev/null; then
      host="$(sed -n 's/.*"host":"\([^"]*\)".*/\1/p' "${ipc_log}" | tail -n1)"
      port="$(sed -n 's/.*"port":\([0-9]*\).*/\1/p' "${ipc_log}" | tail -n1)"
      if [[ -n "${host}" && -n "${port}" ]]; then
        break
      fi
    fi
    sleep 0.1
  done

  if [[ -z "${host}" || -z "${port}" ]]; then
    echo "[replay-text-suite:${fixture_id}] failed: could not parse controller IPC endpoint"
    cat "${ipc_log}" || true
    cleanup_fixture
    return 1
  fi

  tmux new-session -d -x 220 -y 45 -s "${session}" \
    "cd '${ROOT}' && BREADBOARD_IPC_HOST='${host}' BREADBOARD_IPC_PORT='${port}' bun run phaseB/ui.ts"

  local ready=0
  for _ in $(seq 1 120); do
    if ! tmux has-session -t "${session}" 2>/dev/null; then
      break
    fi
    tmux capture-pane -pt "${session}:0.0" -S -240 > "${pane_log}" || true
    if grep -Eq "Enter submit|for shortcuts|OpenTUI slab" "${pane_log}"; then
      ready=1
      break
    fi
    sleep 0.1
  done
  if [[ "${ready}" -ne 1 ]]; then
    echo "[replay-text-suite:${fixture_id}] failed: UI did not become ready"
    cat "${pane_log}" || true
    cleanup_fixture
    return 1
  fi

  local bridge_ready=0
  for _ in $(seq 1 200); do
    if ! tmux has-session -t "${session}" 2>/dev/null; then
      break
    fi
    tmux capture-pane -pt "${session}:0.0" -S -240 > "${pane_log}" || true
    if grep -Eq "bridge=http://127\\.0\\.0\\.1:[0-9]+" "${pane_log}"; then
      bridge_ready=1
      break
    fi
    sleep 0.1
  done
  if [[ "${bridge_ready}" -ne 1 ]]; then
    echo "[replay-text-suite:${fixture_id}] failed: bridge did not become ready"
    echo "--- pane ---"
    cat "${pane_log}" || true
    cleanup_fixture
    return 1
  fi

  tmux send-keys -t "${session}:0.0" C-u
  tmux send-keys -t "${session}:0.0" "replay:${replay_path}"
  tmux send-keys -t "${session}:0.0" Enter

  local found=0
  for _ in $(seq 1 600); do
    if ! tmux has-session -t "${session}" 2>/dev/null; then
      break
    fi
    tmux capture-pane -pt "${session}:0.0" -S -260 > "${pane_log}" || true
    local missing=0
    for token in "${required_tokens[@]}"; do
      if ! grep -Fq "${token}" "${pane_log}"; then
        missing=1
        break
      fi
    done
    if [[ "${missing}" -eq 0 ]]; then
      found=1
      break
    fi
    sleep 0.1
  done

  if [[ "${found}" -ne 1 ]]; then
    echo "[replay-text-suite:${fixture_id}] failed: required tokens not found"
    echo "--- required ---"
    printf '%s\n' "${required_tokens[@]}"
    echo "--- pane ---"
    cat "${pane_log}" || true
    echo "--- controller ---"
    tail -n 120 "${controller_log}" || true
    cleanup_fixture
    return 1
  fi

  cleanup_fixture
  echo "[replay-text-suite:${fixture_id}] pass"
}

echo "[replay-text-suite] artifacts: ${ARTIFACT_DIR}"

run_fixture \
  "context_burst_flush_v1" \
  "config/cli_bridge_replays/phase4/context_burst_flush_smoke_v1.jsonl" \
  "[context] 4 ops" \
  "read_file×2" \
  "context burst smoke complete"

run_fixture \
  "large_output_artifact_v1" \
  "config/cli_bridge_replays/phase4/large_output_artifact_smoke_v1.jsonl" \
  "artifact-sample-output.txt" \
  "Inline output truncated to artifact reference."

run_fixture \
  "large_diff_artifact_v1" \
  "config/cli_bridge_replays/phase4/large_diff_artifact_smoke_v1.jsonl" \
  "artifact-sample-diff.diff" \
  "Large unified diff exported to artifact."

run_fixture \
  "subagents_strip_churn_v1" \
  "config/cli_bridge_replays/phase4/subagents_strip_churn_smoke_v1.jsonl" \
  "Subagent strip churn smoke complete" \
  "Subagents ["

run_fixture \
  "subagents_concurrency_20_v1" \
  "config/cli_bridge_replays/phase4/subagents_concurrency_20_v1.jsonl" \
  "Subagent concurrency 20 replay complete" \
  "Subagents ["

run_fixture \
  "background_cancel_semantics_v1" \
  "config/cli_bridge_replays/phase4/background_cancel_semantics_smoke_v1.jsonl" \
  "cancel" \
  "Background cancel semantics smoke complete"

run_fixture \
  "permission_flow_v1" \
  "config/cli_bridge_replays/phase4/permission_flow_smoke_v1.jsonl" \
  "permission" \
  "approved" \
  "Permission flow smoke complete"

run_fixture \
  "tool_error_retry_v1" \
  "config/cli_bridge_replays/phase4/tool_error_retry_smoke_v1.jsonl" \
  "error" \
  "retry" \
  "Tool retry smoke complete"

echo "[replay-text-suite] pass"
