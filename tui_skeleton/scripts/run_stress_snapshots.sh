#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

CONFIG_PATH="${CONFIG_PATH:-${STRESS_CONFIG:-../agent_configs/opencode_cli_mock_guardrails.yaml}}"
BASE_URL="${BASE_URL:-${BREADBOARD_API_URL:-http://127.0.0.1:9099}}"

echo "[stress] Using config: ${CONFIG_PATH}"
echo "[stress] Using base URL: ${BASE_URL}"

run_repl_script() {
  local script_path="$1"
  local output_path="$2"
  echo "[stress] Running ${script_path} -> ${output_path}"
  BREADBOARD_API_URL="${BASE_URL}" node dist/main.js repl \
    --remote-stream \
    --config "${CONFIG_PATH}" \
    --script "${script_path}" \
    --script-output "${output_path}" \
    --script-final-only
}

run_pty_script() {
  local script_path="$1"
  local output_path="$2"
  local clipboard_payload="$3"
  echo "[stress] Running PTY ${script_path} -> ${output_path}"
  BREADBOARD_FAKE_CLIPBOARD="${clipboard_payload}" \
  BREADBOARD_API_URL="${BASE_URL}" \
  tsx scripts/repl_pty_harness.ts \
    --script "${script_path}" \
    --config "${CONFIG_PATH}" \
    --base-url "${BASE_URL}" \
    --snapshots "${output_path}"
}

# Ensure dist build exists
if [[ ! -d "dist" ]]; then
  echo "[stress] dist/ not found; run npm run build first." >&2
  exit 1
fi

run_repl_script "scripts/mock_hello_script.json" "scripts/mock_hello_output_cli_shared_latest.txt"
run_repl_script "scripts/modal_overlay_stress.json" "scripts/modal_overlay_stress_cli_shared_latest.txt"
run_repl_script "scripts/resize_storm.json" "scripts/resize_storm_cli_shared_latest.txt"
run_repl_script "scripts/paste_flood.json" "scripts/paste_flood_output_cli_shared_latest.txt"
run_repl_script "scripts/token_flood.json" "scripts/token_flood_output_cli_shared_latest.txt"

run_pty_script "scripts/ctrl_v_paste.json" "scripts/ctrl_v_paste_output_cli_shared.txt" "Stress runner clipboard payload"
run_pty_script "scripts/attachment_submit.json" "scripts/attachment_submit_output_cli_shared.txt" "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9YpDkdIAAAAASUVORK5CYII="

echo "[stress] All snapshots refreshed."
