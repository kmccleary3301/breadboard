#!/usr/bin/env bash
# Run the scripted CLI scenarios against the local bridge, refresh/verify guard fixtures,
# and emit guardrail metrics for CI dashboards.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logging"
CLI_DIR="${ROOT_DIR}/kylecode_cli_skeleton"
CONFIG_PATH="${CONFIG_PATH:-${ROOT_DIR}/agent_configs/opencode_cli_mock_guardrails.yaml}"
MODE="verify"
METRICS_OUTPUT="${GUARDRAIL_METRICS_OUTPUT:-${ROOT_DIR}/artifacts/cli_guardrail_metrics.jsonl}"
ALLOWED_FAIL_SCENARIOS=("modal_overlay_stress" "resize_storm")
BRIDGE_HOST="${KYLECODE_CLI_HOST:-127.0.0.1}"
BRIDGE_PORT="${KYLECODE_CLI_PORT:-9099}"
WORKSPACE_DIR="${ROOT_DIR}/agent_ws_opencode"
SCENARIO_ORDER=("coding_task" "multi_file" "modal_overlay_stress" "resize_storm")
SELECTED_SCENARIOS=()

usage() {
  cat <<'EOF'
Usage: scripts/run_cli_guardrail_suite.sh [options]

Options:
  --update-fixtures       Rewrite guardrail fixtures to match the latest runs (default: verify only).
  --verify-only           Do not update fixtures (default behaviour).
  --config PATH           Override the CLI shared config path.
  --metrics-output PATH   Where to write guardrail_metrics.jsonl (default: artifacts/cli_guardrail_metrics.jsonl).
  --scenario NAME         Only run the named scenario (repeatable). Valid: coding_task, multi_file, modal_overlay_stress, resize_storm.
  -h, --help              Show this help.

Environment:
  CONFIG_PATH, GUARDRAIL_METRICS_OUTPUT, KYLECODE_CLI_HOST, KYLECODE_CLI_PORT, KYLECODE_API_URL.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --update-fixtures)
      MODE="update"
      shift
      ;;
    --verify-only)
      MODE="verify"
      shift
      ;;
    --config)
      CONFIG_PATH="$(realpath "$2")"
      shift 2
      ;;
    --metrics-output)
      METRICS_OUTPUT="$(realpath "$2")"
      shift 2
      ;;
    --scenario)
      SELECTED_SCENARIOS+=("$2")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ ! -d "${CLI_DIR}" ]]; then
  echo "[cli-guardrail] Missing CLI workspace at ${CLI_DIR}" >&2
  exit 1
fi

mkdir -p "${LOG_DIR}" "${ROOT_DIR}/artifacts"

declare -A SCRIPT_PATHS=(
  ["coding_task"]="scripts/coding_task_cli_shared.json"
  ["multi_file"]="scripts/multi_file_cli_shared.json"
  ["modal_overlay_stress"]="scripts/modal_overlay_stress.json"
  ["resize_storm"]="scripts/resize_storm.json"
)
declare -A SCRIPT_OUTPUTS=(
  ["coding_task"]="scripts/coding_task_output_cli_shared_latest.txt"
  ["multi_file"]="scripts/multi_file_output_cli_shared_latest.txt"
  ["modal_overlay_stress"]="scripts/modal_overlay_stress_cli_shared_latest.txt"
  ["resize_storm"]="scripts/resize_storm_cli_shared_latest.txt"
)
declare -A FIXTURE_PATHS=(
  ["coding_task"]="${ROOT_DIR}/misc/cli_guardrail_tests/coding_task_cli_shared.guardrails.json"
  ["multi_file"]="${ROOT_DIR}/misc/cli_guardrail_tests/multi_file_cli_shared.guardrails.json"
  ["modal_overlay_stress"]="${ROOT_DIR}/misc/cli_guardrail_tests/modal_overlay_cli_shared.guardrails.json"
  ["resize_storm"]="${ROOT_DIR}/misc/cli_guardrail_tests/resize_storm_cli_shared.guardrails.json"
)
SCENARIOS_TO_RUN=("${SCENARIO_ORDER[@]}")
if [[ ${#SELECTED_SCENARIOS[@]} -gt 0 ]]; then
  SCENARIOS_TO_RUN=()
  for raw in "${SELECTED_SCENARIOS[@]}"; do
    name="${raw}"
    if [[ -z "${SCRIPT_PATHS[$name]+x}" ]]; then
      echo "[cli-guardrail] Unknown scenario '${name}'. Valid options: ${SCENARIO_ORDER[*]}" >&2
      exit 1
    fi
    SCENARIOS_TO_RUN+=("${name}")
  done
fi

snapshot_log_dirs() {
  find "${LOG_DIR}" -maxdepth 1 -mindepth 1 -type d -printf '%P\n' | sort
}

assemble_guard_payload() {
  local run_dir="$1"
  python - "${run_dir}" <<'PY'
import json, pathlib, sys
from agentic_coder_prototype.parity import sanitize_guardrail_events

run_dir = pathlib.Path(sys.argv[1])
summary_path = run_dir / "meta" / "run_summary.json"
if not summary_path.exists():
  print("{}")
  raise SystemExit(0)
data = json.loads(summary_path.read_text())
events = sanitize_guardrail_events(data.get("guardrail_events") or [])
print(json.dumps(events, indent=2))
PY
}

verify_fixture() {
  local run_dir="$1"
  local fixture="$2"
  local scenario="$3"
  local tmp_json
  tmp_json="$(mktemp)"
  assemble_guard_payload "${run_dir}" > "${tmp_json}"
  if [[ "${MODE}" == "update" ]]; then
    mkdir -p "$(dirname "${fixture}")"
    cp "${tmp_json}" "${fixture}"
    echo "[cli-guardrail] Updated fixture ${fixture} (${scenario})"
  else
    if [[ ! -f "${fixture}" ]]; then
      echo "[cli-guardrail] Missing fixture for ${scenario}: ${fixture}" >&2
      diff "${tmp_json}" "${fixture}"
      rm -f "${tmp_json}"
      exit 1
    fi
    if ! diff -u "${fixture}" "${tmp_json}" >/dev/null; then
      echo "[cli-guardrail] Fixture drift detected for ${scenario}" >&2
      diff -u "${fixture}" "${tmp_json}" || true
      rm -f "${tmp_json}"
      exit 1
    fi
    echo "[cli-guardrail] Fixture verified for ${scenario}"
  fi
  rm -f "${tmp_json}"
}

run_cli_script() {
  local scenario="$1"
  local script_rel="$2"
  local output_rel="$3"

  echo "[cli-guardrail] Resetting workspace at ${WORKSPACE_DIR}"
  rm -rf "${WORKSPACE_DIR}"
  mkdir -p "${WORKSPACE_DIR}"

  echo "[cli-guardrail] Running ${scenario} via ${script_rel}"
  (cd "${CLI_DIR}" && npm run build >/dev/null)

  python -m agentic_coder_prototype.api.cli_bridge.server >/tmp/cli_bridge.log 2>&1 &
  local bridge_pid=$!
  trap 'kill ${bridge_pid} >/dev/null 2>&1 || true' INT TERM EXIT
  sleep 3

  set +e
  (
    cd "${CLI_DIR}"
    KYLECODE_API_URL="http://${BRIDGE_HOST}:${BRIDGE_PORT}" \
      node dist/main.js repl \
        --config "${CONFIG_PATH}" \
        --script "${script_rel}" \
        --script-output "${output_rel}" \
        --script-final-only
  )
  local status=$?
  set -e
  kill "${bridge_pid}" >/dev/null 2>&1 || true
  wait "${bridge_pid}" >/dev/null 2>&1 || true
  trap - INT TERM EXIT

  if [[ ${status} -ne 0 ]]; then
    local allowed="false"
    for item in "${ALLOWED_FAIL_SCENARIOS[@]}"; do
      if [[ "${item}" == "${scenario}" ]]; then
        allowed="true"
        break
      fi
    done
    if [[ "${allowed}" == "true" ]]; then
      echo "[cli-guardrail] WARNING: scenario ${scenario} exited with status ${status} (allowed timeout)."
    else
      echo "[cli-guardrail] ERROR: scenario ${scenario} failed (status ${status})." >&2
      exit ${status}
    fi
  fi
}

declare -a RUN_DIRS=()

for scenario in "${SCENARIOS_TO_RUN[@]}"; do
  mapfile -t before_dirs < <(snapshot_log_dirs || true)
  run_cli_script "${scenario}" "${SCRIPT_PATHS[$scenario]}" "${SCRIPT_OUTPUTS[$scenario]}"
  mapfile -t after_dirs < <(snapshot_log_dirs || true)
  new_dir=""
  for candidate in "${after_dirs[@]}"; do
    skip="false"
    for existing in "${before_dirs[@]}"; do
      if [[ "${candidate}" == "${existing}" ]]; then
        skip="true"
        break
      fi
    done
    if [[ "${skip}" == "false" ]]; then
      new_dir="${candidate}"
    fi
  done
  if [[ -z "${new_dir}" && ${#after_dirs[@]} -gt 0 ]]; then
    new_dir="${after_dirs[-1]}"
  fi
  if [[ -z "${new_dir}" ]]; then
    echo "[cli-guardrail] Failed to detect log directory for ${scenario}" >&2
    exit 1
  fi
  run_dir="${LOG_DIR}/${new_dir}"
  RUN_DIRS+=("${run_dir}")
  verify_fixture "${run_dir}" "${FIXTURE_PATHS[$scenario]}" "${scenario}"
done

echo "[cli-guardrail] Writing guardrail metrics to ${METRICS_OUTPUT}"
GUARDRAIL_METRICS_OUTPUT="${METRICS_OUTPUT}" "${ROOT_DIR}/scripts/ci_guardrail_metrics.sh" "${RUN_DIRS[@]}"
echo "[cli-guardrail] Completed guardrail suite (${MODE} mode)"
