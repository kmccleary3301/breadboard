#!/usr/bin/env bash
set -euo pipefail

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

MODE="${ARISTOTLE_INTEGRATION_MODE:-aristotlelib}"
DEFAULT_BASE_URL="https://aristotle.harmonic.fun/api/v1"
SCOPE_PATH="${ARISTOTLE_COMPLIANCE_SCOPE_PATH:-config/compliance/aristotle_permission_scope_v1.json}"

missing=0

check_required() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "MISSING (required): ${name}"
    missing=1
  else
    echo "OK (required): ${name}"
  fi
}

check_optional() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "MISSING (optional): ${name}"
  else
    echo "OK (optional): ${name}"
  fi
}

check_python_module() {
  local module_name="$1"
  if python - <<PY >/dev/null 2>&1
import importlib
importlib.import_module("${module_name}")
PY
  then
    echo "OK (required): python module ${module_name}"
  else
    echo "MISSING (required): python module ${module_name}"
    missing=1
  fi
}

echo "Aristotle E2E readiness check (mode=${MODE})"
check_required "ARISTOTLE_API_KEY"
check_python_module "aristotlelib.api_request"

if [[ "${MODE}" == "aristotlelib" ]]; then
  echo "INFO: aristotlelib mode (project API)"
  echo "INFO: default base URL ${DEFAULT_BASE_URL}"
  check_optional "ARISTOTLE_BASE_URL"
  check_optional "ARISTOTLE_MODEL_ID"
elif [[ "${MODE}" == "openai_shim" ]]; then
  check_required "ARISTOTLE_BASE_URL"
  check_required "ARISTOTLE_MODEL_ID"
else
  echo "ERROR: unknown ARISTOTLE_INTEGRATION_MODE='${MODE}'"
  exit 2
fi

echo "Optional:"
check_optional "ARISTOTLE_ALIAS"
check_optional "ARISTOTLE_PROVIDER_ID"
check_optional "ARISTOTLE_TTL_SECONDS"

if [[ ! -f "${SCOPE_PATH}" ]]; then
  echo "MISSING (required): compliance scope ${SCOPE_PATH}"
  missing=1
else
  if ! python scripts/check_aristotle_permission_scope.py --scope "${SCOPE_PATH}"; then
    echo "NOT READY: compliance scope does not allow internal comparative runs"
    missing=1
  else
    echo "OK (required): compliance scope allows internal comparative runs"
  fi
fi

if [[ "${missing}" -ne 0 ]]; then
  echo "Status: NOT READY"
  exit 2
fi

echo "Status: READY"
