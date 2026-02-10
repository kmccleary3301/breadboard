#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="${ROOT_DIR}/scripts/run_tmux_capture_scenario.py"
COMPARATOR="${ROOT_DIR}/scripts/compare_tmux_run_to_golden.py"
SUMMARY_RENDER="${ROOT_DIR}/scripts/render_tmux_compare_pr_summary.py"
OUT_ROOT="/shared_folders/querylake_server/ray_testing/ray_SCE/docs_tmp/tmux_captures/scenarios"
ACTIONS_ROOT="${TMUX_ACTIONS_ROOT:-${ROOT_DIR}/misc/tui_goldens/tmux_actions}"
THRESHOLDS_PATH="${TMUX_GOLDEN_THRESHOLDS_PATH:-${ROOT_DIR}/config/tmux_golden_thresholds_strict.yaml}"
COMPARE_FAIL_MODE="${TMUX_GOLDEN_FAIL_MODE:-strict}"
ENABLE_PIXEL_COMPARE="${TMUX_GOLDEN_ENABLE_PIXEL:-0}"
MAX_PIXEL_CHANGE_RATIO="${TMUX_GOLDEN_MAX_PIXEL_CHANGE_RATIO:-}"
MAX_PIXEL_MEAN_ABS_DIFF="${TMUX_GOLDEN_MAX_PIXEL_MEAN_ABS_DIFF:-}"

CODEX_TARGET="${1:-}"
CLAUDE_TARGET="${2:-}"
CODEX_GOLDEN_DIR="${3:-${TMUX_GOLDEN_CODEX:-}}"
CLAUDE_GOLDEN_DIR="${4:-${TMUX_GOLDEN_CLAUDE:-}}"

if [[ -z "${CODEX_TARGET}" || -z "${CLAUDE_TARGET}" ]]; then
  echo "Usage: $0 <codex_target> <claude_target> [codex_golden_dir] [claude_golden_dir]" >&2
  echo "Example: $0 breadboard_test_codex_e2e_chatgpt_resume_codex:0.0 breadboard_test_claude_e2e_h45_20260207_145134:0.0" >&2
  exit 2
fi

COMPARE_REPORTS=()

latest_run_dir() {
  local scenario="$1"
  find "${OUT_ROOT}/${scenario}" -name scenario_manifest.json -print | sort | tail -n1 | xargs dirname
}

run_and_compare() {
  local scenario="$1"
  local target="$2"
  local actions_path="$3"
  local label="$4"
  local provider="$5"
  local golden_dir="$6"

  set +e
  python "${RUNNER}" \
    --target "${target}" \
    --scenario "${scenario}" \
    --actions "${actions_path}" \
    --duration 360 \
    --interval 0.5 \
    --out-root "${OUT_ROOT}" \
    --clear-before-send \
    --wait-idle-accept-active-timeout \
    --wait-idle-accept-stable-active-after 8 \
    --must-not-contain "Conversation interrupted" \
    --semantic-timeout 180 \
    --capture-label "${label}"
  local runner_exit=$?
  set -e
  if [[ "${runner_exit}" -ne 0 && "${runner_exit}" -ne 2 ]]; then
    echo "[run-reference] scenario ${scenario} failed with exit=${runner_exit}" >&2
    return "${runner_exit}"
  fi
  if [[ "${runner_exit}" -eq 2 ]]; then
    echo "[run-reference] scenario ${scenario} completed as operational_pass_semantic_fail; continuing"
  fi

  local run_dir
  run_dir="$(latest_run_dir "${scenario}")"
  echo "[run-reference] latest run for ${scenario}: ${run_dir}"

  if [[ -n "${golden_dir}" ]]; then
    local report_json="${run_dir}/comparison_report.json"
    local report_md="${run_dir}/comparison_report.md"
    local compare_args=()
    if [[ "${ENABLE_PIXEL_COMPARE}" == "1" ]]; then
      compare_args+=(--enable-pixel-checks)
    fi
    if [[ -n "${MAX_PIXEL_CHANGE_RATIO}" ]]; then
      compare_args+=(--max-pixel-change-ratio "${MAX_PIXEL_CHANGE_RATIO}")
    fi
    if [[ -n "${MAX_PIXEL_MEAN_ABS_DIFF}" ]]; then
      compare_args+=(--max-pixel-mean-abs-diff "${MAX_PIXEL_MEAN_ABS_DIFF}")
    fi
    python "${COMPARATOR}" \
      --run-dir "${run_dir}" \
      --golden-dir "${golden_dir}" \
      --provider "${provider}" \
      --scenario "${scenario}" \
      --thresholds "${THRESHOLDS_PATH}" \
      --fail-mode "${COMPARE_FAIL_MODE}" \
      --redact \
      --output-json "${report_json}" \
      --output-md "${report_md}" \
      "${compare_args[@]}"
    COMPARE_REPORTS+=("${report_json}")
  else
    echo "[run-reference] no golden dir provided for ${scenario}; skipping compare"
  fi
}

run_and_compare \
  "codex/e2e_compact_semantic_v1" \
  "${CODEX_TARGET}" \
  "${ACTIONS_ROOT}/codex_e2e_compact_semantic_v1.json" \
  "codex_e2e_compact_semantic_v1" \
  "codex" \
  "${CODEX_GOLDEN_DIR}"

run_and_compare \
  "claude/e2e_compact_semantic_v1" \
  "${CLAUDE_TARGET}" \
  "${ACTIONS_ROOT}/claude_e2e_compact_semantic_v1.json" \
  "claude_e2e_compact_semantic_v1" \
  "claude" \
  "${CLAUDE_GOLDEN_DIR}"

if [[ "${#COMPARE_REPORTS[@]}" -gt 0 ]]; then
  args=()
  for report in "${COMPARE_REPORTS[@]}"; do
    args+=(--report "${report}")
  done
  python "${SUMMARY_RENDER}" "${args[@]}"
fi
