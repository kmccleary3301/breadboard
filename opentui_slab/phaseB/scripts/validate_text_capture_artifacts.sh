#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <artifact_dir>"
  exit 2
fi

ARTIFACT_DIR="$1"
if [[ ! -d "$ARTIFACT_DIR" ]]; then
  echo "[capture-validate] missing directory: $ARTIFACT_DIR"
  exit 2
fi

mapfile -t pane_files < <(find "$ARTIFACT_DIR" -maxdepth 1 -type f -name '*_pane.txt' | sort)
if [[ ${#pane_files[@]} -eq 0 ]]; then
  echo "[capture-validate] no pane files in: $ARTIFACT_DIR"
  exit 2
fi

declare -A latest_by_prefix=()
for pane in "${pane_files[@]}"; do
  base="$(basename "$pane")"
  # Example: context_burst_flush_v1_20260219-183605_pane.txt -> context_burst_flush_v1
  prefix="$(echo "$base" | sed -E 's/_[0-9]{8}-[0-9]{6}_pane\.txt$//')"
  # Keep lexicographically latest filename for each logical scenario prefix.
  if [[ -z "${latest_by_prefix[$prefix]:-}" || "$base" > "$(basename "${latest_by_prefix[$prefix]}")" ]]; then
    latest_by_prefix[$prefix]="$pane"
  fi
done

failures=0

check_contains() {
  local file="$1"
  local pattern="$2"
  local label="$3"
  if ! grep -Fq "$pattern" "$file"; then
    echo "[capture-validate] FAIL $label: expected '$pattern' in $(basename "$file")"
    failures=$((failures + 1))
  fi
}

for prefix in "${!latest_by_prefix[@]}"; do
  pane="${latest_by_prefix[$prefix]}"
  base="$(basename "$pane")"

  if grep -Fq "bridge missing" "$pane"; then
    echo "[capture-validate] FAIL bridge: found 'bridge missing' in $base"
    failures=$((failures + 1))
  fi

  case "$prefix" in
    *context_burst*)
      check_contains "$pane" "[context]" "context"
      ;;
  esac

  case "$prefix" in
    *large_output_artifact*|*artifact_render*)
      check_contains "$pane" "artifact" "artifact-output"
      ;;
  esac

  case "$prefix" in
    *large_diff_artifact*|*artifact_render*)
      check_contains "$pane" "tool_diff" "artifact-diff"
      ;;
  esac

  case "$prefix" in
    *footer_hints*)
      check_contains "$pane" "Subagents" "footer-subagents"
      check_contains "$pane" "tasks" "footer-tasks"
      ;;
    *subagents_strip_churn*)
      check_contains "$pane" "Subagents [" "subagents-strip"
      check_contains "$pane" "Subagent strip churn smoke complete" "subagents-strip-finish"
      ;;
    *subagents_concurrency_20*)
      check_contains "$pane" "Subagents [" "subagents-concurrency-strip"
      check_contains "$pane" "Subagent concurrency 20 replay complete" "subagents-concurrency-finish"
      ;;
    *background_cancel_semantics*)
      check_contains "$pane" "cancel" "background-cancel-token"
      check_contains "$pane" "Background cancel semantics smoke complete" "background-cancel-finish"
      ;;
    *permission_flow*)
      check_contains "$pane" "permission" "permission-token"
      check_contains "$pane" "approved" "permission-approved-token"
      check_contains "$pane" "Permission flow smoke complete" "permission-finish"
      ;;
    *tool_error_retry*)
      check_contains "$pane" "error" "retry-error-token"
      check_contains "$pane" "retry" "retry-token"
      check_contains "$pane" "Tool retry smoke complete" "retry-finish"
      ;;
  esac
done

if [[ $failures -ne 0 ]]; then
  echo "[capture-validate] FAIL ($failures)"
  exit 1
fi

echo "[capture-validate] pass (${#latest_by_prefix[@]} scenario(s))"
