#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_PATH="${GUARDRAIL_METRICS_OUTPUT:-${ROOT_DIR}/artifacts/cli_guardrail_metrics.jsonl}"
DEFAULT_LOGS=(
  "${ROOT_DIR}/logging/20251114-221032_agent_ws_opencode"   # coding task
  "${ROOT_DIR}/logging/20251114-232834_agent_ws_opencode"   # multi-file
  "${ROOT_DIR}/logging/20251114-232859_agent_ws_opencode"   # modal overlay
  "${ROOT_DIR}/logging/20251114-232956_agent_ws_opencode"   # resize storm
  "${ROOT_DIR}/logging/20251114-235811_agent_ws_opencode"   # paste flood
  "${ROOT_DIR}/logging/20251114-235830_agent_ws_opencode"   # token flood
  "${ROOT_DIR}/logging/20251115-033640_agent_ws_opencode"   # attachment submit / guard replay
  "${ROOT_DIR}/logging/20251115-050650_agent_ws_opencode"   # python lint fix seed
  "${ROOT_DIR}/logging/20251117-221755_agent_ws_opencode"   # react snapshot seed (TS-MULTI-2 refresh)
  "${ROOT_DIR}/logging/20251117-221741_agent_ws_opencode"   # python newfile seed (PY-NEWFILE-3 refresh)
)

if [[ $# -eq 0 ]]; then
  set -- "${DEFAULT_LOGS[@]}"
fi

LOG_DIRS=()
for candidate in "$@"; do
  if [[ -d "${candidate}" ]]; then
    LOG_DIRS+=("${candidate}")
  fi
done

if [[ ${#LOG_DIRS[@]} -eq 0 ]]; then
  echo "[guardrail-metrics] No logging directories found for patterns: $*" >&2
  exit 0
fi

mkdir -p "$(dirname "${OUTPUT_PATH}")"
echo "[guardrail-metrics] Writing metrics to ${OUTPUT_PATH}"
python "${ROOT_DIR}/scripts/guardrail_metrics.py" "${LOG_DIRS[@]}" --format jsonl > "${OUTPUT_PATH}"
SUMMARY_PATH="${OUTPUT_PATH%.jsonl}.summary.json"
python - "${OUTPUT_PATH}" "${SUMMARY_PATH}" <<'PY'
import json, pathlib, sys

jsonl_path = pathlib.Path(sys.argv[1])
summary_path = pathlib.Path(sys.argv[2])
rows = []
if jsonl_path.exists():
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
exit_counts = {}
event_totals = {}
for row in rows:
    exit_kind = row.get("exit_kind", "unknown")
    exit_counts[exit_kind] = exit_counts.get(exit_kind, 0) + 1
    event_types = str(row.get("event_types", ""))
    if event_types:
        for entry in event_types.split(","):
            if not entry:
                continue
            name, _, count = entry.partition(":")
            try:
                event_totals[name] = event_totals.get(name, 0) + int(count or 0)
            except ValueError:
                continue
payload = {
    "total_runs": len(rows),
    "exit_counts": exit_counts,
    "event_totals": event_totals,
    "runs": [
        {
            "run_dir": row.get("run_dir"),
            "exit_kind": row.get("exit_kind"),
            "steps_taken": row.get("steps_taken"),
            "total_guard_events": row.get("total_guard_events"),
            "event_types": row.get("event_types"),
        }
        for row in rows
    ],
}
summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY
echo "[guardrail-metrics] Complete — $(wc -l < "${OUTPUT_PATH}") rows (summary: ${SUMMARY_PATH})"
