#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${ROOT_DIR}/.." && pwd)"
BRIDGE_SCRIPT="${REPO_ROOT}/scripts/run_cli_bridge_mock.sh"
ARTIFACT_ROOT="${ROOT_DIR}/artifacts/stress"
MOCK_API_KEY="${MOCK_API_KEY:-dummy}"
CASES=(modal_overlay resize_storm_modal permission_rewind model_picker_small layout_ordering tool_ordering_edge ime_placeholder ctrl_v_paste ctrl_v_paste_large paste_undo mock_hello skills_picker skills_picker_ctrl_g ctree_summary)
PTY_CASES=(resize_storm_modal permission_rewind model_picker_small model_picker_provider_filter ctrl_v_paste layout_ordering tool_ordering_edge ime_placeholder ctrl_v_paste_large paste_undo paste_undo_redo file_picker_large file_picker_truncated transcript_viewer_search slash_menu_resize skills_picker skills_picker_ctrl_g ctree_summary)
OVERLAY_CASES=(modal_overlay resize_storm_modal permission_rewind model_picker_small model_picker_provider_filter shortcuts_overlay file_picker_large skills_picker)
RESIZE_CASES=(resize_storm resize_storm_modal)
if [[ -n "${STRESS_CI_CASES:-}" ]]; then
  # shellcheck disable=SC2206
  CASES=(${STRESS_CI_CASES})
fi
CONFIG_PATH="${CONFIG_PATH:-${STRESS_CONFIG:-../agent_configs/opencode_cli_mock_guardrails.yaml}}"
HOST="${BREADBOARD_CI_HOST:-127.0.0.1}"
PORT="${BREADBOARD_CI_PORT:-9099}"
BASE_URL="http://${HOST}:${PORT}"
GUARD_LOGS=(
  "${REPO_ROOT}/logging/20251114-221032_agent_ws_opencode"
  "${REPO_ROOT}/logging/20251114-232859_agent_ws_opencode"
)
TTFT_BUDGET_MS="${TTFT_BUDGET_MS:-2500}"
SPINNER_BUDGET_HZ="${SPINNER_BUDGET_HZ:-12}"
MIN_SSE_EVENTS="${MIN_SSE_EVENTS:-0}"
MAX_TIMELINE_WARNINGS="${MAX_TIMELINE_WARNINGS:-1}"
RESIZE_EVENT_BUDGET="${RESIZE_EVENT_BUDGET:-50}"
RESIZE_BURST_BUDGET_MS="${RESIZE_BURST_BUDGET_MS:-8000}"
MAX_LINES_CHANGED_PCT="${MAX_LINES_CHANGED_PCT:-}"
P95_LINES_CHANGED_PCT="${P95_LINES_CHANGED_PCT:-}"
MAX_GHOST_LINES="${MAX_GHOST_LINES:-0}"
MAX_FLICKER_EVENTS="${MAX_FLICKER_EVENTS:-0}"
MAX_ANOMALIES="${MAX_ANOMALIES:-0}"
OVERLAY_MAX_LINES_CHANGED_PCT="${OVERLAY_MAX_LINES_CHANGED_PCT:-}"
OVERLAY_P95_LINES_CHANGED_PCT="${OVERLAY_P95_LINES_CHANGED_PCT:-}"
OVERLAY_MAX_FLICKER_EVENTS="${OVERLAY_MAX_FLICKER_EVENTS:-}"
RESIZE_MAX_LINES_CHANGED_PCT="${RESIZE_MAX_LINES_CHANGED_PCT:-}"
RESIZE_P95_LINES_CHANGED_PCT="${RESIZE_P95_LINES_CHANGED_PCT:-}"
RESIZE_MAX_FLICKER_EVENTS="${RESIZE_MAX_FLICKER_EVENTS:-}"
BREADBOARD_CONTRACT_REQUIRE_SEQ="${BREADBOARD_CONTRACT_REQUIRE_SEQ:-1}"
BREADBOARD_CONTRACT_REQUIRE_DATA="${BREADBOARD_CONTRACT_REQUIRE_DATA:-1}"
BREADBOARD_CONTRACT_REQUIRE_TIMESTAMP_MS="${BREADBOARD_CONTRACT_REQUIRE_TIMESTAMP_MS:-1}"
TIMELINE_ANALYZER="${ROOT_DIR}/tools/timeline/checkBudgets.mjs"
KEY_FUZZ_ITERATIONS="${STRESS_CI_KEY_FUZZ_ITERATIONS:-1}"
KEY_FUZZ_STEPS="${STRESS_CI_KEY_FUZZ_STEPS:-60}"
KEY_FUZZ_SEED="${STRESS_CI_KEY_FUZZ_SEED:-0}"
AUTO_START_LIVE="${STRESS_CI_AUTO_START_LIVE:-0}"
TMUX_CAPTURE_TARGET="${BREADBOARD_TMUX_CAPTURE_TARGET:-}"
TMUX_CAPTURE_SCALE="${BREADBOARD_TMUX_CAPTURE_SCALE:-1}"

if [[ ! -f "${TIMELINE_ANALYZER}" ]]; then
  echo "[stress:ci] Missing timeline analyzer at ${TIMELINE_ANALYZER}" >&2
  exit 1
fi

declare -a REQUIRED_PTY_FILES=(
  "pty_snapshots.txt"
  "pty_plain.txt"
  "pty_raw.ansi"
  "pty_metadata.json"
  "pty_frames.ndjson"
  "pty_manifest.json"
  "input_log.ndjson"
  "repl_state.ndjson"
  "events.ndjson"
  "config.json"
  "grid_snapshots/active.txt"
  "grid_snapshots/final.txt"
  "grid_snapshots/final_vs_active.diff"
  "grid_deltas.ndjson"
  "anomalies.json"
  "timeline.ndjson"
  "timeline_summary.json"
  "timeline_flamegraph.txt"
  "ttydoc.txt"
  "case_info.json"
  "contract_report.json"
  "metrics.json"
)

declare -a REQUIRED_REPL_FILES=(
  "transcript.txt"
  "cli.log"
  "sse_events.txt"
  "events.ndjson"
  "config.json"
  "repl_state.ndjson"
  "timeline.ndjson"
  "timeline_summary.json"
  "timeline_flamegraph.txt"
  "ttydoc.txt"
  "case_info.json"
  "contract_report.json"
  "metrics.json"
)

BRIDGE_PID=""
NEW_BATCH=""

cleanup() {
  if [[ -n "${BRIDGE_PID}" ]] && kill -0 "${BRIDGE_PID}" 2>/dev/null; then
    kill "${BRIDGE_PID}" 2>/dev/null || true
    wait "${BRIDGE_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

wait_for_port() {
  local attempt
  for attempt in {1..60}; do
    if python - <<'PY'
import os, socket, sys
host = os.environ["BREADBOARD_WAIT_HOST"]
port = int(os.environ["BREADBOARD_WAIT_PORT"])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(1)
    try:
        sock.connect((host, port))
    except OSError:
        sys.exit(1)
PY
    then
      return 0
    fi
    sleep 1
  done
  echo "[stress:ci] FastAPI bridge failed to start on ${HOST}:${PORT}" >&2
  exit 1
}

mkdir -p "${ARTIFACT_ROOT}"

if [[ ! -x "${BRIDGE_SCRIPT}" ]]; then
  echo "[stress:ci] Missing bridge launcher at ${BRIDGE_SCRIPT}" >&2
  exit 1
fi

if [[ "${AUTO_START_LIVE}" != "1" ]]; then
  echo "[stress:ci] Starting FastAPI bridge on ${HOST}:${PORT}"
  BREADBOARD_CLI_HOST="${HOST}" \
  BREADBOARD_CLI_PORT="${PORT}" \
  "${BRIDGE_SCRIPT}" >"${REPO_ROOT}/logging/cli_bridge_ci.log" 2>&1 &
  BRIDGE_PID=$!

  export BREADBOARD_WAIT_HOST="${HOST}"
  export BREADBOARD_WAIT_PORT="${PORT}"
  export BREADBOARD_CONTRACT_REQUIRE_SEQ
  export BREADBOARD_CONTRACT_REQUIRE_DATA
  export BREADBOARD_CONTRACT_REQUIRE_TIMESTAMP_MS
  wait_for_port
  unset BREADBOARD_WAIT_HOST BREADBOARD_WAIT_PORT
else
  echo "[stress:ci] Auto-start live bridge enabled; skipping manual bridge launch."
fi

echo "[stress:ci] Running bundle subset: ${CASES[*]}"
CASES_ARGS=()
for case in "${CASES[@]}"; do
  CASES_ARGS+=("--case" "${case}")
done
CASE_MOCK_SSE_FLAG=()
if [[ "${STRESS_CI_CASE_MOCK_SSE:-1}" != "0" ]]; then
  CASE_MOCK_SSE_FLAG+=("--case-mock-sse")
else
  CASE_MOCK_SSE_FLAG+=("--no-case-mock-sse")
fi
INCLUDE_LIVE_FLAG=()
if [[ "${STRESS_CI_INCLUDE_LIVE:-0}" == "1" ]]; then
  INCLUDE_LIVE_FLAG+=("--include-live")
fi
AUTO_START_FLAG=()
if [[ "${AUTO_START_LIVE}" == "1" ]]; then
  AUTO_START_FLAG+=("--auto-start-live")
fi
TMUX_CAPTURE_FLAGS=()
if [[ -n "${TMUX_CAPTURE_TARGET}" ]]; then
  TMUX_CAPTURE_FLAGS+=("--tmux-capture-target" "${TMUX_CAPTURE_TARGET}")
  TMUX_CAPTURE_FLAGS+=("--tmux-capture-scale" "${TMUX_CAPTURE_SCALE}")
fi
GUARD_ARGS=()
for dir in "${GUARD_LOGS[@]}"; do
  if [[ -d "${dir}" ]]; then
    GUARD_ARGS+=("--guard-log" "${dir}")
  fi
done
npm run stress:bundle -- \
  --config "${CONFIG_PATH}" \
  --base-url "${BASE_URL}" \
  "${CASES_ARGS[@]}" \
  "${GUARD_ARGS[@]}" \
  "${CASE_MOCK_SSE_FLAG[@]}" \
  "${INCLUDE_LIVE_FLAG[@]}" \
  "${AUTO_START_FLAG[@]}" \
  "${TMUX_CAPTURE_FLAGS[@]}" \
  --key-fuzz-iterations "${KEY_FUZZ_ITERATIONS}" \
  --key-fuzz-steps "${KEY_FUZZ_STEPS}" \
  --key-fuzz-seed "${KEY_FUZZ_SEED}"

if [[ -d "${ARTIFACT_ROOT}" ]]; then
  NEW_BATCH="$(ls -1dt "${ARTIFACT_ROOT}"/*/ 2>/dev/null | head -n 1 | sed 's#/$##')"
fi

if [[ -z "${NEW_BATCH}" ]] || [[ ! -d "${NEW_BATCH}" ]]; then
  echo "[stress:ci] Failed to locate artifacts after bundle run" >&2
  exit 1
fi

ZIP_SOURCE="${NEW_BATCH}.zip"
if [[ -f "${ZIP_SOURCE}" ]]; then
  cp "${ZIP_SOURCE}" "${ARTIFACT_ROOT}/stress_ci_latest.zip"
fi

echo "[stress:ci] Verifying artifacts under ${NEW_BATCH}"
require_file() {
  local file="$1"
  if [[ ! -f "${file}" ]]; then
    echo "[stress:ci] Missing artifact ${file}" >&2
    exit 1
  fi
}

require_dir() {
  local dir="$1"
  if [[ ! -d "${dir}" ]]; then
    echo "[stress:ci] Missing directory ${dir}" >&2
    exit 1
  fi
}

check_anomalies_empty() {
  local file="$1"
  MAX_ALLOW="${MAX_ANOMALIES}" python - "$file" <<'PY'
import json, os, pathlib, sys
path = pathlib.Path(sys.argv[1])
max_allow = int(os.environ.get("MAX_ALLOW", "0"))
if not path.exists():
    print(f"[stress:ci] Missing anomalies file {path}", file=sys.stderr)
    sys.exit(1)
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except json.JSONDecodeError as exc:
    print(f"[stress:ci] Invalid JSON in {path}: {exc}", file=sys.stderr)
    sys.exit(1)
count = len(data) if isinstance(data, list) else None
if count is None:
    print(f"[stress:ci] Unexpected anomalies payload in {path}", file=sys.stderr)
    sys.exit(1)
if count <= max_allow:
    sys.exit(0)
print(f"[stress:ci] Layout anomalies detected in {path}: {count} (budget {max_allow})", file=sys.stderr)
sys.exit(1)
PY
}

check_timeline_budget() {
  local summary_file="$1"
  local case_name="$2"
  local warnings_file="${NEW_BATCH}/timeline_budget_warnings.jsonl"
  local max_lines_pct="${MAX_LINES_CHANGED_PCT}"
  local p95_lines_pct="${P95_LINES_CHANGED_PCT}"
  local max_flicker="${MAX_FLICKER_EVENTS}"
  if is_resize_case "${case_name}"; then
    if [[ -n "${RESIZE_MAX_LINES_CHANGED_PCT}" ]]; then
      max_lines_pct="${RESIZE_MAX_LINES_CHANGED_PCT}"
    fi
    if [[ -n "${RESIZE_P95_LINES_CHANGED_PCT}" ]]; then
      p95_lines_pct="${RESIZE_P95_LINES_CHANGED_PCT}"
    fi
    if [[ -n "${RESIZE_MAX_FLICKER_EVENTS}" ]]; then
      max_flicker="${RESIZE_MAX_FLICKER_EVENTS}"
    fi
  elif is_overlay_case "${case_name}"; then
    if [[ -n "${OVERLAY_MAX_LINES_CHANGED_PCT}" ]]; then
      max_lines_pct="${OVERLAY_MAX_LINES_CHANGED_PCT}"
    fi
    if [[ -n "${OVERLAY_P95_LINES_CHANGED_PCT}" ]]; then
      p95_lines_pct="${OVERLAY_P95_LINES_CHANGED_PCT}"
    fi
    if [[ -n "${OVERLAY_MAX_FLICKER_EVENTS}" ]]; then
      max_flicker="${OVERLAY_MAX_FLICKER_EVENTS}"
    fi
  fi
  local extra_flags=()
  if [[ -n "${max_lines_pct}" ]]; then
    extra_flags+=("--max-lines-pct" "${max_lines_pct}")
  fi
  if [[ -n "${p95_lines_pct}" ]]; then
    extra_flags+=("--p95-lines-pct" "${p95_lines_pct}")
  fi
  if [[ -n "${MAX_GHOST_LINES}" ]]; then
    extra_flags+=("--max-ghost-lines" "${MAX_GHOST_LINES}")
  fi
  if [[ -n "${max_flicker}" ]]; then
    extra_flags+=("--max-flicker-events" "${max_flicker}")
  fi
  node "${TIMELINE_ANALYZER}" \
    --summary "${summary_file}" \
    --case "${case_name}" \
    --ttft-ms "${TTFT_BUDGET_MS}" \
    --spinner-hz "${SPINNER_BUDGET_HZ}" \
    --min-sse "${MIN_SSE_EVENTS}" \
    --max-warnings "${MAX_TIMELINE_WARNINGS}" \
    --resize-events "${RESIZE_EVENT_BUDGET}" \
    --resize-burst-ms "${RESIZE_BURST_BUDGET_MS}" \
    --warnings-file "${warnings_file}" \
    "${extra_flags[@]}"
}

print_chaos_metadata() {
  local case_info="$1"
  local case_name="$2"
  if [[ ! -f "${case_info}" ]]; then
    echo "[stress:ci] Missing case_info.json for ${case_name} (expected chaos metadata)" >&2
    return
  fi
  python - "${case_info}" "${case_name}" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
case = sys.argv[2]
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"[stress:ci] chaos metadata read failed for {case}: {exc}")
    raise SystemExit(0)
chaos = payload.get("chaos")
if chaos:
    print(f"[stress:ci] {case} chaos={json.dumps(chaos)}")
else:
    print(f"[stress:ci] {case} chaos=none")
PY
}

is_pty_case() {
  local candidate="$1"
  for entry in "${PTY_CASES[@]}"; do
    if [[ "${candidate}" == "${entry}" ]]; then
      return 0
    fi
  done
  return 1
}

is_overlay_case() {
  local candidate="$1"
  for entry in "${OVERLAY_CASES[@]}"; do
    if [[ "${candidate}" == "${entry}" ]]; then
      return 0
    fi
  done
  return 1
}

is_resize_case() {
  local candidate="$1"
  for entry in "${RESIZE_CASES[@]}"; do
    if [[ "${candidate}" == "${entry}" ]]; then
      return 0
    fi
  done
  return 1
}

for case in "${CASES[@]}"; do
  case_dir="${NEW_BATCH}/${case}"
  if [[ ! -d "${case_dir}" ]]; then
    echo "[stress:ci] Missing case directory ${case_dir}" >&2
    exit 1
  fi
  if is_pty_case "${case}"; then
    require_dir "${case_dir}/grid_snapshots"
    for relative in "${REQUIRED_PTY_FILES[@]}"; do
      require_file "${case_dir}/${relative}"
    done
    check_anomalies_empty "${case_dir}/anomalies.json"
    check_timeline_budget "${case_dir}/timeline_summary.json" "${case}"
    print_chaos_metadata "${case_dir}/case_info.json" "${case}"
  else
    for relative in "${REQUIRED_REPL_FILES[@]}"; do
      require_file "${case_dir}/${relative}"
    done
    check_timeline_budget "${case_dir}/timeline_summary.json" "${case}"
    print_chaos_metadata "${case_dir}/case_info.json" "${case}"
  fi
done

require_file "${NEW_BATCH}/manifest.json"
MANIFEST_GUARD_REQUIRED="$(
python - "${NEW_BATCH}/manifest.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
print(1 if payload.get("guardrailMetrics") else 0)
PY
)"
GUARD_SUMMARY_REQUIRED="${MANIFEST_GUARD_REQUIRED}"
if [[ "${GUARD_SUMMARY_REQUIRED}" == "1" ]]; then
  for case in "${CASES[@]}"; do
    require_file "${NEW_BATCH}/${case}/guardrail_summary.json"
  done
fi
if [[ -f "${NEW_BATCH}/guardrail_metrics.jsonl" ]]; then
  require_file "${NEW_BATCH}/guardrail_metrics.summary.json"
fi

CLIPBOARD_STRICT="${STRESS_CI_CLIPBOARD_STRICT:-0}"
if [[ -f "${NEW_BATCH}/clipboard_diff_report.txt" ]]; then
  echo "[stress:ci] Clipboard diff report:"
  sed -n '1,80p' "${NEW_BATCH}/clipboard_diff_report.txt" || true
else
  echo "[stress:ci] No clipboard_diff_report.txt found; skipping report snippet."
fi
python - "${NEW_BATCH}/manifest.json" "${CLIPBOARD_STRICT}" <<'PY'
import json, pathlib, sys

manifest = pathlib.Path(sys.argv[1])
strict = sys.argv[2] == "1"
data = json.loads(manifest.read_text(encoding="utf-8"))
diffs = data.get("clipboardDiffs", []) or []
if not diffs:
    sys.exit(0)
print(f"[stress:ci] clipboard diffs detected for {[entry['caseId'] for entry in diffs]}")
for entry in diffs:
    print(f"[stress:ci] {entry['caseId']}: diff file {entry['diffFile']}")
if strict:
    print("[stress:ci] clipboard diff strict mode enabled — failing build", file=sys.stderr)
    sys.exit(1)
PY

KEY_FUZZ_SUMMARY="${NEW_BATCH}/key_fuzz/summary.json"
if [[ -f "${KEY_FUZZ_SUMMARY}" ]]; then
  python - "${KEY_FUZZ_SUMMARY}" <<'PY'
import json, pathlib, sys
summary = pathlib.Path(sys.argv[1])
try:
    payload = json.loads(summary.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"[stress:ci] key-fuzz summary read failed: {exc}")
    raise SystemExit(0)
fail_count = len(payload.get("failures", []))
iterations = payload.get("iterations")
run_dir = payload.get("runDir")
print(f"[stress:ci] key-fuzz iterations={iterations} failures={fail_count} run={run_dir}")
if fail_count:
    print(f"[stress:ci] key-fuzz failures: {' '.join(payload.get('failures', []))}", file=sys.stderr)
    raise SystemExit(1)
PY
fi

echo "[stress:ci] Completed successfully — artifacts in ${NEW_BATCH}"
