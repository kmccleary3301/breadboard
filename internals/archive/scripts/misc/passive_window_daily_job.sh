#!/usr/bin/env bash
set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ARTIFACT_DIR="${REPO_ROOT}/artifacts"
LOG_DIR="${ARTIFACT_DIR}/nightly_archive/cron_logs"
mkdir -p "${LOG_DIR}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
log_file="${LOG_DIR}/passive_window_daily_${timestamp}.log"

exec > >(tee -a "${log_file}") 2>&1

echo "[passive-window] start: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[passive-window] repo: ${REPO_ROOT}"
echo "[passive-window] args: --require-stable-window"

if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
  PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
else
  echo "[passive-window] no python interpreter found"
  exit 127
fi
echo "[passive-window] python: ${PYTHON_BIN}"

cd "${REPO_ROOT}"
source_args=()
if [[ -n "${PASSIVE_WINDOW_SOURCE_DIRS:-}" ]]; then
  IFS=':' read -r -a source_dir_array <<< "${PASSIVE_WINDOW_SOURCE_DIRS}"
  for source_dir in "${source_dir_array[@]}"; do
    if [[ -n "${source_dir}" ]]; then
      source_args+=(--source-dir "${source_dir}")
    fi
  done
fi
"${PYTHON_BIN}" scripts/run_passive_window_daily.py --require-stable-window "${source_args[@]}"
rc=$?

mapped_rc="${rc}"
if [[ "${rc}" -eq 2 ]]; then
  echo "[passive-window] stable-window pending (rc=2), mapped to success for scheduler"
  mapped_rc=0
fi

alert_level="error"
alert_message="passive-window daily job failed"
status_json="${ARTIFACT_DIR}/nightly_archive/passive_4day_latest_status.json"
if [[ "${rc}" -eq 2 && -f "${status_json}" ]]; then
  status_triplet="$("${PYTHON_BIN}" - "${status_json}" <<'PY'
import json
import sys
path = sys.argv[1]
try:
    payload = json.loads(open(path, "r", encoding="utf-8").read())
except Exception:
    print("unknown|unknown|unknown")
    raise SystemExit(0)
atp_ok = bool(payload.get("atp_ok"))
evolake_ok = bool(payload.get("evolake_ok"))
classification = str((payload.get("seven_day") or {}).get("classification", "unknown"))
print(f"{atp_ok}|{evolake_ok}|{classification}")
PY
)"
  IFS='|' read -r atp_ok evolake_ok seven_day_classification <<<"${status_triplet}"
  if [[ "${atp_ok}" == "True" || "${atp_ok}" == "true" ]] && [[ "${evolake_ok}" == "True" || "${evolake_ok}" == "true" ]] && [[ "${seven_day_classification}" == "failed_window" ]]; then
    alert_level="warning"
    alert_message="stable-window pending; day green but seven-day window not yet satisfied"
  else
    alert_level="error"
    alert_message="stable-window check failed with non-green day (atp_ok=${atp_ok}, evolake_ok=${evolake_ok}, classification=${seven_day_classification})"
  fi
fi

if [[ -n "${PASSIVE_WINDOW_ALERT_CMD:-}" && "${rc}" -ne 0 ]]; then
  PASSIVE_WINDOW_ALERT_LEVEL="${alert_level}" \
  PASSIVE_WINDOW_ALERT_RC="${rc}" \
  PASSIVE_WINDOW_ALERT_MESSAGE="${alert_message}" \
  PASSIVE_WINDOW_ALERT_LOG_FILE="${log_file}" \
  bash -lc "${PASSIVE_WINDOW_ALERT_CMD}" || true
fi

echo "[passive-window] end: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[passive-window] rc: ${rc}"
echo "[passive-window] mapped_rc: ${mapped_rc}"
exit "${mapped_rc}"
