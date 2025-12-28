#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/capture_opencode_golden.sh --scenario <name> --prompt <text> [options] [-- <extra opencode args...>]

Options:
  --version <ver>        OpenCode version label for output dir (default: read from industry_refs/opencode)
  --model <provider/model>  OpenCode model (default: openai/gpt-5.1-codex-mini)
  --agent <name>         OpenCode agent name (default: none)
  --run-id <id>          Unique run id for this capture (default: UTC timestamp)
  --fixture-dir <path>   Copy this directory into the scenario workspace before running
  --force                Overwrite existing run directory (same --run-id)

Notes:
  - Captures under: misc/opencode_runs/goldens/<version>/<scenario>/runs/<run-id>/
  - Runs OpenCode from `industry_refs/opencode` with an isolated HOME + XDG dirs.
  - Requires instrumented OpenCode (sets OPENCODE_PROVIDER_DUMP_DIR to capture raw provider request bodies).
  - Sources .env if present (expects OPENAI_API_KEY / ANTHROPIC_API_KEY / OPENROUTER_API_KEY as needed).
EOF
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SCENARIO=""
PROMPT=""
VERSION=""
MODEL="openai/gpt-5.1-codex-mini"
AGENT=""
FIXTURE_DIR=""
RUN_ID=""
FORCE="false"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      VERSION="${2:-}"; shift 2 ;;
    --model)
      MODEL="${2:-}"; shift 2 ;;
    --agent)
      AGENT="${2:-}"; shift 2 ;;
    --run-id)
      RUN_ID="${2:-}"; shift 2 ;;
    --fixture-dir)
      FIXTURE_DIR="${2:-}"; shift 2 ;;
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

OPENCODE_REPO_ROOT="${ROOT_DIR}/industry_refs/opencode"
OPENCODE_ENTRYPOINT="${OPENCODE_REPO_ROOT}/packages/opencode/src/index.ts"
OPENCODE_PACKAGE_JSON="${OPENCODE_REPO_ROOT}/packages/opencode/package.json"
OPENCODE_TSCONFIG="${OPENCODE_REPO_ROOT}/packages/opencode/tsconfig.json"
OPENCODE_BUNFIG="${OPENCODE_REPO_ROOT}/packages/opencode/bunfig.toml"

if [[ ! -f "${OPENCODE_ENTRYPOINT}" ]]; then
  echo "[capture-opencode-golden] Missing OpenCode entrypoint: ${OPENCODE_ENTRYPOINT}" >&2
  exit 2
fi
if [[ ! -f "${OPENCODE_TSCONFIG}" ]]; then
  echo "[capture-opencode-golden] Missing OpenCode tsconfig: ${OPENCODE_TSCONFIG}" >&2
  exit 2
fi
if [[ ! -f "${OPENCODE_BUNFIG}" ]]; then
  echo "[capture-opencode-golden] Missing OpenCode bunfig: ${OPENCODE_BUNFIG}" >&2
  exit 2
fi

if [[ -z "${VERSION}" ]]; then
  VERSION="$(python - "${OPENCODE_PACKAGE_JSON}" <<'PY'
import json
import sys
path = sys.argv[1]
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)
print(data.get("version") or "unknown")
PY
)"
fi

SCEN_BASE_DIR="${ROOT_DIR}/misc/opencode_runs/goldens/${VERSION}/${SCENARIO}"
RUN_ID="${RUN_ID:-$(date -u +"%Y%m%d_%H%M%S")}"
RUN_DIR="${SCEN_BASE_DIR}/runs/${RUN_ID}"

mkdir -p "${SCEN_BASE_DIR}"

if [[ -d "${RUN_DIR}" ]]; then
  if [[ "${FORCE}" != "true" ]]; then
    echo "[capture-opencode-golden] Refusing to overwrite existing run: ${RUN_DIR}" >&2
    echo "Pass --force to overwrite this run id, or pass a different --run-id." >&2
    exit 1
  fi
  rm -rf "${RUN_DIR}"
fi

mkdir -p "${RUN_DIR}/"{provider_dumps,normalized,workspace,home,exports}

# Seed workspace fixture, if provided.
if [[ -n "${FIXTURE_DIR}" ]]; then
  SRC_DIR="${ROOT_DIR}/${FIXTURE_DIR}"
  if [[ ! -d "${SRC_DIR}" ]]; then
    echo "[capture-opencode-golden] fixture dir not found: ${SRC_DIR}" >&2
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

# Ensure OpenCode deps are present (monorepo root).
if [[ ! -d "${OPENCODE_REPO_ROOT}/node_modules" ]]; then
  echo "[capture-opencode-golden] Installing OpenCode deps (bun install)..." >&2
  (cd "${OPENCODE_REPO_ROOT}" && bun install)
fi

OPENCODE_COMMIT="$(git -C "${OPENCODE_REPO_ROOT}" rev-parse HEAD 2>/dev/null || true)"
TS_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

python - "${RUN_DIR}/scenario.json" "${SCENARIO}" "${VERSION}" "${MODEL}" "${AGENT}" "${OPENCODE_COMMIT}" "${TS_UTC}" "${RUN_ID}" "${EXTRA_ARGS[@]-}" <<'PY'
import json
import sys

out_path = sys.argv[1]
scenario = sys.argv[2]
version_label = sys.argv[3]
model = sys.argv[4]
agent = sys.argv[5]
commit = sys.argv[6]
captured_at_utc = sys.argv[7]
run_id = sys.argv[8]
extra_args = sys.argv[9:]

payload = {
    "scenario": scenario,
    "version_label": version_label,
    "model": model,
    "agent": agent or None,
    "opencode_commit": commit or None,
    "captured_at_utc": captured_at_utc,
    "run_id": run_id,
    "extra_args": extra_args,
}

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2, sort_keys=True)
    f.write("\n")
PY

HOME_DIR="${RUN_DIR}/home"
export HOME="${HOME_DIR}"
export XDG_CONFIG_HOME="${HOME_DIR}/.config"
export XDG_DATA_HOME="${HOME_DIR}/.local/share"
export XDG_CACHE_HOME="${HOME_DIR}/.cache"
export XDG_STATE_HOME="${HOME_DIR}/.local/state"

# Ensure Bun picks up the correct JSX + path alias config when running from the
# isolated workspace directory (Bun otherwise defaults to react/jsx runtime).
cat >"${RUN_DIR}/workspace/tsconfig.json" <<EOF
{
  "\$schema": "https://json.schemastore.org/tsconfig",
  "extends": "${OPENCODE_TSCONFIG}"
}
EOF

# Provider dump logger (instrumented OpenCode).
export OPENCODE_PROVIDER_DUMP_DIR="${RUN_DIR}/provider_dumps"
export OPENCODE_PROVIDER_DUMP_FLUSH_TIMEOUT_MS="60000"
export OPENCODE_PROVIDER_DUMP_MAX_BYTES="8388608"

# Reduce variance / avoid interactive prompts.
export OPENCODE_DISABLE_DEFAULT_PLUGINS="1"
export OPENCODE_DISABLE_LSP_DOWNLOAD="1"

# Allow overriding Bun conditions for environments where `--conditions=browser`
# breaks CJS interop (e.g., debug shim). Default remains "browser".
BUN_CONDITIONS="${OPENCODE_BUN_CONDITIONS:-browser}"
export OPENCODE_DISABLE_AUTOUPDATE="1"
export OPENCODE_PERMISSION='{"edit":"allow","bash":"allow","skill":"allow","webfetch":"allow","doom_loop":"allow","external_directory":"allow"}'

pushd "${RUN_DIR}/workspace" >/dev/null
set +e
if [[ -n "${AGENT}" ]]; then
  printf "%s" "${PROMPT}" | bun run --config="${OPENCODE_BUNFIG}" --conditions="${BUN_CONDITIONS}" "${OPENCODE_ENTRYPOINT}" run \
    --format json \
    --model "${MODEL}" \
    --agent "${AGENT}" \
    --title "${SCENARIO}_${RUN_ID}" \
    "${EXTRA_ARGS[@]}" \
    >"${RUN_DIR}/stdout.jsonl" \
    2>"${RUN_DIR}/stderr.txt"
else
  printf "%s" "${PROMPT}" | bun run --config="${OPENCODE_BUNFIG}" --conditions="${BUN_CONDITIONS}" "${OPENCODE_ENTRYPOINT}" run \
    --format json \
    --model "${MODEL}" \
    --title "${SCENARIO}_${RUN_ID}" \
    "${EXTRA_ARGS[@]}" \
    >"${RUN_DIR}/stdout.jsonl" \
    2>"${RUN_DIR}/stderr.txt"
fi
RUN_EXIT_CODE="$?"
set -e
popd >/dev/null

echo "${RUN_EXIT_CODE}" >"${RUN_DIR}/exit_code.txt"
if [[ "${RUN_EXIT_CODE}" != "0" ]]; then
  echo "[capture-opencode-golden] warning: opencode exited with ${RUN_EXIT_CODE}" >&2
fi

SESSION_ID="$(python - "${RUN_DIR}/stdout.jsonl" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    print("")
    raise SystemExit(0)

session_id = ""
for raw in path.read_text(encoding="utf-8").splitlines():
    raw = raw.strip()
    if not raw:
        continue
    try:
        payload = json.loads(raw)
    except Exception:
        continue
    session_id = payload.get("sessionID") or ""
    if session_id:
        break
print(session_id)
PY
)"
echo "${SESSION_ID}" >"${RUN_DIR}/session_id.txt"

# Export OpenCode session (best-effort).
if [[ -n "${SESSION_ID}" ]]; then
  pushd "${RUN_DIR}/workspace" >/dev/null
  set +e
  bun run --config="${OPENCODE_BUNFIG}" --conditions="${BUN_CONDITIONS}" "${OPENCODE_ENTRYPOINT}" export "${SESSION_ID}" \
    >"${RUN_DIR}/exports/opencode_export.json" \
    2>>"${RUN_DIR}/stderr.txt"
  EXPORT_EXIT_CODE="$?"
  set -e
  popd >/dev/null
  echo "${EXPORT_EXIT_CODE}" >"${RUN_DIR}/exports/export_exit_code.txt"

  if [[ -f "${RUN_DIR}/exports/opencode_export.json" ]]; then
    python "${ROOT_DIR}/scripts/convert_opencode_export_to_replay_session.py" \
      --in "${RUN_DIR}/exports/opencode_export.json" \
      --out "${RUN_DIR}/exports/replay_session.json" \
      >>"${RUN_DIR}/stderr.txt" 2>&1 || true
  fi
fi

python "${ROOT_DIR}/scripts/sanitize_provider_logs.py" "${RUN_DIR}/provider_dumps" >>"${RUN_DIR}/stderr.txt" 2>&1 || true
python "${ROOT_DIR}/scripts/process_provider_dumps.py" \
  --input-dir "${RUN_DIR}/provider_dumps" \
  --output-dir "${RUN_DIR}/normalized" \
  >>"${RUN_DIR}/stderr.txt" 2>&1 || true

if [[ -e "${SCEN_BASE_DIR}/current" && ! -L "${SCEN_BASE_DIR}/current" ]]; then
  rm -rf "${SCEN_BASE_DIR}/current"
fi
ln -sfn "${RUN_DIR}" "${SCEN_BASE_DIR}/current"
echo "[capture-opencode-golden] Done: ${RUN_DIR}"
