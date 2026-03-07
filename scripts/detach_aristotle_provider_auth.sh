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
  echo "Skipping detach: ARISTOTLE_INTEGRATION_MODE=${MODE} (no provider-auth attach used)." >&2
  exit 0
fi

ENGINE_URL="${BREADBOARD_ENGINE_URL:-http://127.0.0.1:9099}"
PROVIDER_ID="${ARISTOTLE_PROVIDER_ID:-openai}"
ALIAS="${ARISTOTLE_ALIAS:-aristotle}"
AUTH_HEADER=()
if [[ -n "${BREADBOARD_API_TOKEN:-}" ]]; then
  AUTH_HEADER=(-H "Authorization: Bearer ${BREADBOARD_API_TOKEN}")
fi

payload="$(cat <<JSON
{
  "provider_id": "${PROVIDER_ID}",
  "alias": "${ALIAS}"
}
JSON
)"

curl -fsS \
  "${AUTH_HEADER[@]}" \
  -H "Content-Type: application/json" \
  -X POST "${ENGINE_URL}/v1/provider-auth/detach" \
  -d "${payload}" >/dev/null

echo "Detached provider auth for provider='${PROVIDER_ID}' alias='${ALIAS}'."
