#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPORT_DIR="${ROOT_DIR}/artifacts/maintenance"
REPORT_PATH="${REPORT_DIR}/devx_full_pass_latest.json"
TMP_QUICKSTART="$(mktemp)"
TMP_CAPABILITIES="$(mktemp)"
TMP_ONBOARDING_CONTRACT="$(mktemp)"
START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PROFILE="${BREADBOARD_FULL_PASS_PROFILE:-full}"

now_ms() {
  python3 - <<'PY'
import time
print(int(time.time() * 1000))
PY
}

case "${PROFILE}" in
  full|engine)
    ;;
  *)
    echo "[devx-full-pass] invalid BREADBOARD_FULL_PASS_PROFILE=${PROFILE}; expected full|engine" >&2
    exit 2
    ;;
esac

cleanup() {
  rm -f "${TMP_QUICKSTART}" || true
  rm -f "${TMP_CAPABILITIES}" || true
  rm -f "${TMP_ONBOARDING_CONTRACT}" || true
}
trap cleanup EXIT

mkdir -p "${REPORT_DIR}"
cd "${ROOT_DIR}"

echo "[devx-full-pass] quickstart snapshot"
python scripts/dev/quickstart_first_time.py --json >"${TMP_QUICKSTART}"
python scripts/dev/cli_capabilities.py --json >"${TMP_CAPABILITIES}"
python scripts/dev/check_onboarding_contract_drift.py --json --strict >"${TMP_ONBOARDING_CONTRACT}"

echo "[devx-full-pass] bootstrap with all checks (profile=${PROFILE})"
BOOTSTRAP_STARTED_MS="$(now_ms)"
if [[ "${PROFILE}" == "engine" ]]; then
  bash scripts/dev/bootstrap_first_time.sh --profile engine --all-checks
else
  bash scripts/dev/bootstrap_first_time.sh --all-checks
fi
BOOTSTRAP_ENDED_MS="$(now_ms)"

echo "[devx-full-pass] onboarding command-path smoke with live lane"
SMOKE_STARTED_MS="$(now_ms)"
bash scripts/dev/devx_smoke.sh \
  --profile "${PROFILE}" \
  --live \
  --skip-onboarding-contract \
  --skip-quickstart-helper
SMOKE_ENDED_MS="$(now_ms)"

END_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python - <<'PY' "${TMP_QUICKSTART}" "${TMP_CAPABILITIES}" "${TMP_ONBOARDING_CONTRACT}" "${REPORT_PATH}" "${START_TS}" "${END_TS}" "${PROFILE}" "${BOOTSTRAP_STARTED_MS}" "${BOOTSTRAP_ENDED_MS}" "${SMOKE_STARTED_MS}" "${SMOKE_ENDED_MS}"
import json
import sys
from pathlib import Path

quickstart_path = Path(sys.argv[1])
capabilities_path = Path(sys.argv[2])
onboarding_contract_path = Path(sys.argv[3])
report_path = Path(sys.argv[4])
start_ts = sys.argv[5]
end_ts = sys.argv[6]
profile = sys.argv[7]
bootstrap_started_ms = int(sys.argv[8])
bootstrap_ended_ms = int(sys.argv[9])
smoke_started_ms = int(sys.argv[10])
smoke_ended_ms = int(sys.argv[11])

quickstart = json.loads(quickstart_path.read_text())
capabilities = json.loads(capabilities_path.read_text())
onboarding_contract = json.loads(onboarding_contract_path.read_text())
report = {
    "status": "ok",
    "started_at_utc": start_ts,
    "ended_at_utc": end_ts,
    "profile": profile,
    "quickstart_snapshot": quickstart,
    "cli_capabilities_snapshot": capabilities,
    "onboarding_contract_snapshot": onboarding_contract,
    "timing": {
        "bootstrap_duration_ms": max(0, bootstrap_ended_ms - bootstrap_started_ms),
        "devx_smoke_duration_ms": max(0, smoke_ended_ms - smoke_started_ms),
        "bootstrap_started_ms": bootstrap_started_ms,
        "bootstrap_ended_ms": bootstrap_ended_ms,
        "devx_smoke_started_ms": smoke_started_ms,
        "devx_smoke_ended_ms": smoke_ended_ms,
    },
    "steps": [
        "quickstart_snapshot",
        "cli_capabilities_snapshot",
        "onboarding_contract_snapshot",
        "bootstrap_all_checks",
        "devx_smoke_live",
    ],
}
report_path.write_text(json.dumps(report, indent=2) + "\n")
print(f"[devx-full-pass] report: {report_path}")
PY

echo "[devx-full-pass] complete"
