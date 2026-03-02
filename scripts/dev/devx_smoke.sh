#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_LIVE=0
PROFILE="full"
SKIP_ONBOARDING_CONTRACT=0
SKIP_QUICKSTART_HELPER=0
BREADBOARD_OK=1
HAS_TUI_SOURCE=0
DOCTOR_FIRST_TIME_OK=0
SETUP_PROFILE_OK=0

usage() {
  cat <<'EOF'
Usage: scripts/dev/devx_smoke.sh [--live] [--profile <full|engine>]

Options:
  --live                 Also run live SDK hello verification.
  --profile <full|engine>  Validation profile. full is default.
  --skip-onboarding-contract  Skip onboarding contract drift check.
  --skip-quickstart-helper    Skip quickstart helper snapshot.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --live)
      RUN_LIVE=1
      shift
      ;;
    --profile)
      PROFILE="$2"
      shift 2
      ;;
    --skip-onboarding-contract)
      SKIP_ONBOARDING_CONTRACT=1
      shift
      ;;
    --skip-quickstart-helper)
      SKIP_QUICKSTART_HELPER=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[devx-smoke] unknown arg: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "${PROFILE}" in
  full|engine)
    ;;
  *)
    echo "[devx-smoke] invalid --profile: ${PROFILE}" >&2
    usage >&2
    exit 2
    ;;
esac

cd "${ROOT_DIR}"

if [[ "${SKIP_ONBOARDING_CONTRACT}" == "0" ]]; then
  echo "[devx-smoke] onboarding contract drift check"
  python scripts/dev/check_onboarding_contract_drift.py --strict
else
  echo "[devx-smoke] onboarding contract drift check skipped (--skip-onboarding-contract)"
fi
echo "[devx-smoke] profile=${PROFILE}"

if [[ -f "${ROOT_DIR}/tui_skeleton/package.json" ]]; then
  HAS_TUI_SOURCE=1
fi

if ! breadboard --help >/tmp/breadboard_devx_help.txt 2>&1; then
  BREADBOARD_OK=0
fi

if [[ "${BREADBOARD_OK}" == "1" ]]; then
  if breadboard doctor --help 2>/tmp/breadboard_doctor_help.err | grep -q -- "--first-time"; then
    DOCTOR_FIRST_TIME_OK=1
  fi
  if breadboard setup --help 2>/tmp/breadboard_setup_help.err | grep -q -- "--profile"; then
    SETUP_PROFILE_OK=1
  fi
fi

echo "[devx-smoke] first-time doctor profiles"
python scripts/dev/first_time_doctor.py --profile engine --strict
if [[ "${PROFILE}" == "full" && "${HAS_TUI_SOURCE}" == "1" && "${BREADBOARD_OK}" == "1" ]]; then
  if python scripts/dev/first_time_doctor.py --profile full --strict; then
    python scripts/dev/first_time_doctor.py --profile tui --strict
  else
    echo "[devx-smoke] warning: full profile strict doctor failed; continuing with engine-first checks"
  fi
elif [[ "${PROFILE}" == "full" && "${HAS_TUI_SOURCE}" == "1" ]]; then
  echo "[devx-smoke] skipping full/tui doctor profiles: breadboard CLI is unavailable"
fi

if [[ "${SKIP_QUICKSTART_HELPER}" == "0" ]]; then
  echo "[devx-smoke] quickstart helper"
  python scripts/dev/quickstart_first_time.py --json >/tmp/breadboard_quickstart.json
else
  echo "[devx-smoke] quickstart helper skipped (--skip-quickstart-helper)"
fi

if [[ "${BREADBOARD_OK}" == "1" ]]; then
  if [[ "${DOCTOR_FIRST_TIME_OK}" == "1" ]]; then
    echo "[devx-smoke] doctor first-time command variants"
    breadboard doctor --first-time --first-time-profile engine
    if [[ "${PROFILE}" == "full" && "${HAS_TUI_SOURCE}" == "1" ]]; then
      breadboard doctor --first-time --first-time-profile full
      breadboard doctor --first-time --first-time-profile tui
    fi
  else
    echo "[devx-smoke] skipping doctor --first-time variants: CLI does not support these flags"
  fi

  if [[ "${SETUP_PROFILE_OK}" == "1" ]]; then
    echo "[devx-smoke] setup profile passthrough"
    breadboard setup --profile engine --skip-node --no-doctor
    if [[ "${PROFILE}" == "full" && "${HAS_TUI_SOURCE}" == "1" ]]; then
      breadboard setup --profile tui --skip-python --no-doctor
    fi
  else
    echo "[devx-smoke] skipping setup --profile passthrough: CLI does not support this command/flags"
  fi
else
  echo "[devx-smoke] breadboard CLI unavailable; validating bootstrap script directly"
  bash scripts/dev/bootstrap_first_time.sh --profile engine --no-doctor
fi

if [[ "${RUN_LIVE}" == "1" ]]; then
  echo "[devx-smoke] running live sdk hello lane"
  args=()
  if [[ "${PROFILE}" == "engine" ]]; then
    args+=(--no-ts)
  fi
  ./scripts/dev/sdk_hello_live_smoke.sh "${args[@]}"
fi

echo "[devx-smoke] complete"
