#!/usr/bin/env bash
set -euo pipefail

# Load repo-local .env (if present) so ARISTOTLE_* values work without manual export.
if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

MODE="${ARISTOTLE_INTEGRATION_MODE:-aristotlelib}"
if [[ "${MODE}" != "openai_shim" ]]; then
  echo "This script is only for ARISTOTLE_INTEGRATION_MODE=openai_shim." >&2
  echo "Current mode=${MODE}. In aristotlelib mode, use scripts/aristotlelib_smoke.py instead." >&2
  exit 2
fi

if [[ -z "${ARISTOTLE_API_KEY:-}" ]]; then
  echo "ARISTOTLE_API_KEY is required" >&2
  exit 2
fi

ENGINE_URL="${BREADBOARD_ENGINE_URL:-http://127.0.0.1:9099}"
PROVIDER_ID="${ARISTOTLE_PROVIDER_ID:-openai}"
ALIAS="${ARISTOTLE_ALIAS:-aristotle}"
TTL_SECONDS="${ARISTOTLE_TTL_SECONDS:-7200}"
BASE_URL="${ARISTOTLE_BASE_URL:-https://aristotle.harmonic.fun/api/v1}"
AUTH_HEADER=()
if [[ -n "${BREADBOARD_API_TOKEN:-}" ]]; then
  AUTH_HEADER=(-H "Authorization: Bearer ${BREADBOARD_API_TOKEN}")
fi

payload="$(cat <<JSON
{
  "material": {
    "provider_id": "${PROVIDER_ID}",
    "alias": "${ALIAS}",
    "api_key": "${ARISTOTLE_API_KEY}",
    "base_url": "${BASE_URL}",
    "ttl_seconds": ${TTL_SECONDS},
    "is_subscription_plan": false
  }
}
JSON
)"

curl -fsS \
  "${AUTH_HEADER[@]}" \
  -H "Content-Type: application/json" \
  -X POST "${ENGINE_URL}/v1/provider-auth/attach" \
  -d "${payload}" >/dev/null

upper_provider="$(printf '%s' "${PROVIDER_ID}" | tr '[:lower:]' '[:upper:]')"
echo "Attached Aristotle auth material to provider '${PROVIDER_ID}' alias '${ALIAS}'."
echo "Using base_url='${BASE_URL}'."
echo "Export this before running sessions:"
echo "  export BREADBOARD_PROVIDER_AUTH_ALIAS_${upper_provider}=${ALIAS}"
echo "Check status with:"
echo "  curl -fsS ${ENGINE_URL}/v1/provider-auth/status | jq"
