#!/usr/bin/env bash
set -euo pipefail

BRIDGE_DIR_DEFAULT="/shared_folders/querylake_server/ray_testing/ray_SCE/atp_passive_window_sources"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VALIDATOR="${REPO_ROOT}/scripts/validate_passive_window_source_drop.py"

usage() {
  cat <<'EOF'
Usage:
  ingest_passive_window_source_drop.sh \
    --atp-file /path/to/atp_ops_digest.latest.json \
    --evolake-file /path/to/evolake_toy_campaign_nightly.local.json

Options:
  --atp-file PATH          ATP digest JSON file to ingest.
  --evolake-file PATH      EvoLake nightly JSON file to ingest.
  --evolake-local-dir DIR  Optional dir containing EvoLake local run JSONs.
  --bridge-dir DIR         Destination bridge dir (default: atp_passive_window_sources).
  --no-trigger             Do not trigger passive-window service after ingest.
  --dry-run                Show what would be copied.
  -h, --help               Show this message.
EOF
}

ATP_FILE=""
EVOLAKE_FILE=""
EVOLAKE_LOCAL_DIR=""
BRIDGE_DIR="${BRIDGE_DIR_DEFAULT}"
TRIGGER_SERVICE=1
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --atp-file)
      ATP_FILE="${2:-}"
      shift 2
      ;;
    --evolake-file)
      EVOLAKE_FILE="${2:-}"
      shift 2
      ;;
    --evolake-local-dir)
      EVOLAKE_LOCAL_DIR="${2:-}"
      shift 2
      ;;
    --bridge-dir)
      BRIDGE_DIR="${2:-}"
      shift 2
      ;;
    --no-trigger)
      TRIGGER_SERVICE=0
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${ATP_FILE}" || -z "${EVOLAKE_FILE}" ]]; then
  echo "Both --atp-file and --evolake-file are required." >&2
  usage >&2
  exit 2
fi
if [[ ! -f "${ATP_FILE}" ]]; then
  echo "Missing ATP file: ${ATP_FILE}" >&2
  exit 2
fi
if [[ ! -f "${EVOLAKE_FILE}" ]]; then
  echo "Missing EvoLake file: ${EVOLAKE_FILE}" >&2
  exit 2
fi
if [[ -n "${EVOLAKE_LOCAL_DIR}" && ! -d "${EVOLAKE_LOCAL_DIR}" ]]; then
  echo "Missing EvoLake local dir: ${EVOLAKE_LOCAL_DIR}" >&2
  exit 2
fi

echo "[ingest] bridge_dir=${BRIDGE_DIR}"
echo "[ingest] atp_file=${ATP_FILE}"
echo "[ingest] evolake_file=${EVOLAKE_FILE}"

if [[ "${DRY_RUN}" -eq 1 ]]; then
  echo "[ingest] dry-run enabled; no files copied."
else
  mkdir -p "${BRIDGE_DIR}"
  cp "${ATP_FILE}" "${BRIDGE_DIR}/"
  cp "${EVOLAKE_FILE}" "${BRIDGE_DIR}/"

  if [[ -n "${EVOLAKE_LOCAL_DIR}" ]]; then
    mkdir -p "${BRIDGE_DIR}/evolake_toy_campaign_local"
    shopt -s nullglob
    local_jsons=("${EVOLAKE_LOCAL_DIR}"/*.json)
    shopt -u nullglob
    if [[ ${#local_jsons[@]} -gt 0 ]]; then
      cp "${local_jsons[@]}" "${BRIDGE_DIR}/evolake_toy_campaign_local/"
      echo "[ingest] copied ${#local_jsons[@]} evolake local jsons."
    else
      echo "[ingest] no local evolake jsons found in ${EVOLAKE_LOCAL_DIR}."
    fi
  fi
fi

validator_cmd=(python3 "${VALIDATOR}" --source-dir "${BRIDGE_DIR}" --require-green)
echo "[ingest] validating source drop..."
if [[ "${DRY_RUN}" -eq 1 ]]; then
  echo "[ingest] dry-run command: ${validator_cmd[*]}"
else
  "${validator_cmd[@]}"
fi

if [[ "${TRIGGER_SERVICE}" -eq 1 ]]; then
  trigger_cmd=(systemctl --user start breadboard-passive-window-daily.service)
  echo "[ingest] triggering passive-window service..."
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[ingest] dry-run command: ${trigger_cmd[*]}"
  else
    "${trigger_cmd[@]}"
  fi
fi

echo "[ingest] done."
