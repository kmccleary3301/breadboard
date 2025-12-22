#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/capture_claude_golden.sh --scenario <name> --prompt <text> [options] [-- <extra claude args...>]

Options:
  --version <ver>          Claude Code version label for output dir (default: 2.0.72)
  --model <alias|name>     Claude model alias/name (default: haiku)
  --max-budget-usd <amt>   Claude Code print-mode budget cap (default: 0.25)
  --run-id <id>            Unique run id for this capture (default: UTC timestamp)
  --fixture-dir <path>     Copy this directory into the scenario workspace before running
  --session-persistence    Enable session persistence (default: disabled via --no-session-persistence)
  --force                 Overwrite existing run directory (same --run-id)

Notes:
  - Captures under: misc/claude_code_runs/goldens/<version>/<scenario>/runs/<run-id>/
  - Runs claude-code-logged with an isolated HOME, provider dump dir, and workspace fixture dir.
  - Sources .env if present (expects ANTHROPIC_API_KEY).
  - Extra args after `--` are passed through to claude-code-logged.
EOF
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

VERSION="2.0.72"
MODEL="haiku"
MAX_BUDGET_USD="0.25"
SCENARIO=""
PROMPT=""
FIXTURE_DIR=""
SESSION_PERSISTENCE="false"
FORCE="false"
RUN_ID=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      VERSION="${2:-}"; shift 2 ;;
    --model)
      MODEL="${2:-}"; shift 2 ;;
    --max-budget-usd)
      MAX_BUDGET_USD="${2:-}"; shift 2 ;;
    --run-id)
      RUN_ID="${2:-}"; shift 2 ;;
    --fixture-dir)
      FIXTURE_DIR="${2:-}"; shift 2 ;;
    --session-persistence)
      SESSION_PERSISTENCE="true"; shift ;;
    --scenario)
      SCENARIO="${2:-}"; shift 2 ;;
    --prompt)
      PROMPT="${2:-}"; shift 2 ;;
    --force)
      FORCE="true"; shift ;;
    --help|-h)
      usage; exit 0 ;;
    --)
      shift
      EXTRA_ARGS=("$@")
      break ;;
    *)
      EXTRA_ARGS+=("$1")
      shift ;;
  esac
done

if [[ -z "${SCENARIO}" || -z "${PROMPT}" ]]; then
  usage
  exit 2
fi

SCEN_BASE_DIR="${ROOT_DIR}/misc/claude_code_runs/goldens/${VERSION}/${SCENARIO}"
RUN_ID="${RUN_ID:-$(date -u +"%Y%m%d_%H%M%S")}"
RUN_DIR="${SCEN_BASE_DIR}/runs/${RUN_ID}"

mkdir -p "${SCEN_BASE_DIR}"

# Migrate legacy captures (flat scenario dir) into a dedicated run dir so that
# every invocation gets an isolated folder.
if [[ -d "${SCEN_BASE_DIR}/provider_dumps" || -d "${SCEN_BASE_DIR}/workspace" || -d "${SCEN_BASE_DIR}/normalized" || -d "${SCEN_BASE_DIR}/home" ]]; then
  LEGACY_ID="legacy_$(date -u +"%Y%m%d_%H%M%S")"
  LEGACY_DIR="${SCEN_BASE_DIR}/runs/${LEGACY_ID}"
  mkdir -p "${LEGACY_DIR}"
  for name in provider_dumps normalized workspace home stdout.json stderr.txt exit_code.txt scenario.json; do
    if [[ -e "${SCEN_BASE_DIR}/${name}" ]]; then
      mv "${SCEN_BASE_DIR}/${name}" "${LEGACY_DIR}/"
    fi
  done
fi

if [[ -d "${RUN_DIR}" ]]; then
  if [[ "${FORCE}" != "true" ]]; then
    echo "[capture-claude-golden] Refusing to overwrite existing run: ${RUN_DIR}" >&2
    echo "Pass --force to overwrite this run id, or pass a different --run-id." >&2
    exit 1
  fi
  rm -rf "${RUN_DIR}"
fi

mkdir -p "${RUN_DIR}/"{provider_dumps,normalized,workspace,home}

# Seed workspace fixture, if provided.
if [[ -n "${FIXTURE_DIR}" ]]; then
  SRC_DIR="${ROOT_DIR}/${FIXTURE_DIR}"
  if [[ ! -d "${SRC_DIR}" ]]; then
    echo "[capture-claude-golden] fixture dir not found: ${SRC_DIR}" >&2
    exit 2
  fi
  cp -a "${SRC_DIR}/." "${RUN_DIR}/workspace/"
fi

# Convenience: load API keys if .env exists.
if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi

CLI_VERSION="$("${ROOT_DIR}/tools/claude_code_logged/dist/claude-code-logged" --version 2>/dev/null || true)"
TS_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

python - "${RUN_DIR}/scenario.json" "${SCENARIO}" "${VERSION}" "${MODEL}" "${MAX_BUDGET_USD}" "${CLI_VERSION}" "${TS_UTC}" "${RUN_ID}" "${EXTRA_ARGS[@]-}" <<'PY'
import json
import sys

out_path = sys.argv[1]
scenario = sys.argv[2]
version_label = sys.argv[3]
model = sys.argv[4]
max_budget_usd = sys.argv[5]
cli_version = sys.argv[6]
captured_at_utc = sys.argv[7]
run_id = sys.argv[8]
extra_args = sys.argv[9:]

payload = {
    "scenario": scenario,
    "version_label": version_label,
    "model": model,
    "max_budget_usd": max_budget_usd,
    "cli_version": cli_version,
    "captured_at_utc": captured_at_utc,
    "run_id": run_id,
    "extra_args": extra_args,
}

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2, sort_keys=True)
    f.write("\n")
PY

pushd "${RUN_DIR}/workspace" >/dev/null
set +e
CLAUDE_CODE_LOG_DIR="${RUN_DIR}/provider_dumps" \
CLAUDE_CODE_HOME="${RUN_DIR}/home" \
CLAUDE_CODE_WORKSPACE="${RUN_DIR}/workspace" \
CLAUDE_CODE_SESSION_ID="${SCENARIO}_${RUN_ID}" \
  "${ROOT_DIR}/scripts/run_claude_code_logged.sh" \
    -p "${PROMPT}" \
    --output-format json \
    --model "${MODEL}" \
    --max-budget-usd "${MAX_BUDGET_USD}" \
    $(if [[ "${SESSION_PERSISTENCE}" != "true" ]]; then echo "--no-session-persistence"; fi) \
    "${EXTRA_ARGS[@]}" \
    >"${RUN_DIR}/stdout.json" \
    2>"${RUN_DIR}/stderr.txt"
RUN_EXIT_CODE="$?"
set -e
popd >/dev/null

python "${ROOT_DIR}/scripts/process_provider_dumps.py" \
  --input-dir "${RUN_DIR}/provider_dumps" \
  --output-dir "${RUN_DIR}/normalized"

echo "${RUN_EXIT_CODE}" >"${RUN_DIR}/exit_code.txt"
if [[ "${RUN_EXIT_CODE}" != "0" ]]; then
  echo "[capture-claude-golden] warning: claude-code-logged exited with ${RUN_EXIT_CODE}" >&2
fi

if [[ -e "${SCEN_BASE_DIR}/current" && ! -L "${SCEN_BASE_DIR}/current" ]]; then
  rm -rf "${SCEN_BASE_DIR}/current"
fi
ln -sfn "${RUN_DIR}" "${SCEN_BASE_DIR}/current"
echo "[capture-claude-golden] Done: ${RUN_DIR}"
