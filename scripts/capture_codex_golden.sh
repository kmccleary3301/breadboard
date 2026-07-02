#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/capture_codex_golden.sh --scenario <name> (--prompt <text> | --prompt-file <path>) [options] [-- <extra codex args...>]

Options:
  --version <ver>         Codex CLI version label for output dir (default: from `codex --version`)
  --model <name>          Codex model id (default: Codex configured model)
  --reasoning-effort <v>  Codex reasoning effort override (default: high; set empty to disable)
  --isolate-home          Run Codex with isolated HOME/XDG dirs under the run directory
  --sandbox <mode>        Codex sandbox mode (default: danger-full-access)
  --allow-empty-tools     Allow converted replay output even when no tool calls are present
  --run-id <id>           Unique run id for this capture (default: UTC timestamp)
  --fixture-dir <path>    Copy this directory into the scenario workspace before running
  --force                 Overwrite existing run directory (same --run-id)

Notes:
  - Captures under: misc/codex_cli_runs/goldens/<version>/<scenario>/runs/<run-id>/
  - Sources .env if present (expects OPENAI_API_KEY or other Codex-compatible auth env).
  - Writes raw rollout JSONL + converted replay session JSON.
EOF
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  if [[ "${PYTHON_BIN}" == */* ]]; then
    if [[ ! -x "${PYTHON_BIN}" ]]; then
      echo "[capture-codex-golden] PYTHON_BIN is not executable: ${PYTHON_BIN}" >&2
      exit 127
    fi
  elif command -v -- "${PYTHON_BIN}" >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v -- "${PYTHON_BIN}")"
  else
    echo "[capture-codex-golden] PYTHON_BIN command not found: ${PYTHON_BIN}" >&2
    exit 127
  fi
elif [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
else
  echo "[capture-codex-golden] no Python interpreter found" >&2
  exit 127
fi

VERSION=""
MODEL=""
REASONING_EFFORT="high"
SANDBOX_MODE="danger-full-access"
SCENARIO=""
PROMPT=""
PROMPT_FILE=""
FIXTURE_DIR=""
RUN_ID=""
FORCE="false"
ALLOW_EMPTY_TOOLS="false"
ISOLATE_HOME="false"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      VERSION="${2:-}"; shift 2 ;;
    --model)
      MODEL="${2:-}"; shift 2 ;;
    --reasoning-effort)
      REASONING_EFFORT="${2:-}"; shift 2 ;;
    --sandbox)
      SANDBOX_MODE="${2:-}"; shift 2 ;;
    --run-id)
      RUN_ID="${2:-}"; shift 2 ;;
    --allow-empty-tools)
      ALLOW_EMPTY_TOOLS="true"; shift ;;
    --isolate-home)
      ISOLATE_HOME="true"; shift ;;
    --fixture-dir)
      FIXTURE_DIR="${2:-}"; shift 2 ;;
    --scenario)
      SCENARIO="${2:-}"; shift 2 ;;
    --prompt)
      PROMPT="${2:-}"; shift 2 ;;
    --prompt-file)
      PROMPT_FILE="${2:-}"; shift 2 ;;
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

if [[ -z "${SCENARIO}" ]]; then
  usage
  exit 2
fi
if [[ -n "${PROMPT}" && -n "${PROMPT_FILE}" ]]; then
  echo "[capture-codex-golden] pass only one of --prompt or --prompt-file" >&2
  exit 2
fi
if [[ -z "${PROMPT}" && -z "${PROMPT_FILE}" ]]; then
  usage
  exit 2
fi

if [[ -n "${PROMPT_FILE}" ]]; then
  SRC_PROMPT_FILE="${ROOT_DIR}/${PROMPT_FILE}"
  if [[ ! -f "${SRC_PROMPT_FILE}" ]]; then
    echo "[capture-codex-golden] prompt file not found: ${SRC_PROMPT_FILE}" >&2
    exit 2
  fi
  PROMPT="$(cat "${SRC_PROMPT_FILE}")"
fi

if [[ -z "${VERSION}" ]]; then
  VERSION="$(codex --version 2>/dev/null | awk '{print $2}')"
  VERSION="${VERSION:-unknown}"
fi

SCEN_BASE_DIR="${ROOT_DIR}/misc/codex_cli_runs/goldens/${VERSION}/${SCENARIO}"
RUN_ID="${RUN_ID:-$(date -u +"%Y%m%d_%H%M%S")}"
RUN_DIR="${SCEN_BASE_DIR}/runs/${RUN_ID}"

mkdir -p "${SCEN_BASE_DIR}"

if [[ -d "${RUN_DIR}" ]]; then
  if [[ "${FORCE}" != "true" ]]; then
    echo "[capture-codex-golden] Refusing to overwrite existing run: ${RUN_DIR}" >&2
    echo "Pass --force to overwrite this run id, or pass a different --run-id." >&2
    exit 1
  fi
  rm -rf "${RUN_DIR}"
fi

mkdir -p "${RUN_DIR}/"{workspace,exports,home}

if [[ -n "${FIXTURE_DIR}" ]]; then
  SRC_DIR="${ROOT_DIR}/${FIXTURE_DIR}"
  if [[ ! -d "${SRC_DIR}" ]]; then
    echo "[capture-codex-golden] fixture dir not found: ${SRC_DIR}" >&2
    exit 2
  fi
  cp -a "${SRC_DIR}/." "${RUN_DIR}/workspace/"
fi

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi

CODEX_VERSION_RAW="$(codex --version 2>/dev/null || true)"
TS_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

"${PYTHON_BIN}" - "${RUN_DIR}/scenario.json" "${SCENARIO}" "${VERSION}" "${MODEL}" "${REASONING_EFFORT}" "${SANDBOX_MODE}" "${CODEX_VERSION_RAW}" "${TS_UTC}" "${RUN_ID}" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} <<'PY'
import json
import sys

out_path = sys.argv[1]
scenario = sys.argv[2]
version_label = sys.argv[3]
model = sys.argv[4]
reasoning_effort = sys.argv[5]
sandbox_mode = sys.argv[6]
codex_version_raw = sys.argv[7]
captured_at_utc = sys.argv[8]
run_id = sys.argv[9]
extra_args = sys.argv[10:]

payload = {
    "scenario": scenario,
    "version_label": version_label,
    "model": model if model else None,
    "reasoning_effort": reasoning_effort if reasoning_effort else None,
    "sandbox_mode": sandbox_mode,
    "codex_version_raw": codex_version_raw,
    "captured_at_utc": captured_at_utc,
    "run_id": run_id,
    "extra_args": extra_args,
}

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2, sort_keys=True)
    f.write("\n")
PY

if [[ "${ISOLATE_HOME}" == "true" ]]; then
  # Optional isolation mode for deterministic local config state.
  HOME_DIR="${RUN_DIR}/home"
  export HOME="${HOME_DIR}"
  export XDG_CONFIG_HOME="${HOME_DIR}/.config"
  export XDG_DATA_HOME="${HOME_DIR}/.local/share"
  export XDG_CACHE_HOME="${HOME_DIR}/.cache"
  export XDG_STATE_HOME="${HOME_DIR}/.local/state"
fi

CODEX_ARGS=()
if [[ -n "${REASONING_EFFORT}" ]]; then
  CODEX_ARGS+=( -c "model_reasoning_effort=\"${REASONING_EFFORT}\"" )
fi
if [[ -n "${MODEL}" ]]; then
  CODEX_ARGS+=( --model "${MODEL}" )
fi

pushd "${RUN_DIR}/workspace" >/dev/null
set +e
codex exec --json \
  --skip-git-repo-check \
  --sandbox "${SANDBOX_MODE}" \
  ${CODEX_ARGS[@]+"${CODEX_ARGS[@]}"} \
  ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
  "${PROMPT}" \
  >"${RUN_DIR}/rollout.jsonl" \
  2>"${RUN_DIR}/stderr.txt" \
  </dev/null
RUN_EXIT_CODE="$?"
set -e
popd >/dev/null

echo "${RUN_EXIT_CODE}" >"${RUN_DIR}/exit_code.txt"
if [[ "${RUN_EXIT_CODE}" != "0" ]]; then
  echo "[capture-codex-golden] codex exec exited with ${RUN_EXIT_CODE}" >&2
  exit "${RUN_EXIT_CODE}"
fi

if [[ ! -s "${RUN_DIR}/rollout.jsonl" ]]; then
  echo "[capture-codex-golden] rollout.jsonl missing or empty" >&2
  exit 1
fi

"${PYTHON_BIN}" - "${RUN_DIR}/rollout.jsonl" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
error_events = []
non_error_events = 0
for raw in path.read_text(encoding="utf-8").splitlines():
    raw = raw.strip()
    if not raw:
        continue
    try:
        payload = json.loads(raw)
    except Exception:
        non_error_events += 1
        continue
    event_type = str(payload.get("type") or payload.get("event") or "")
    if event_type == "error" or event_type.endswith(".failed") or event_type.endswith("_failed"):
        error_events.append(payload.get("message") or payload.get("error") or payload)
    elif payload.get("error"):
        error_events.append(payload.get("error"))
    else:
        non_error_events += 1

if error_events:
    print("[capture-codex-golden] codex emitted error event(s):", file=sys.stderr)
    for event in error_events:
        print(f"  - {event}", file=sys.stderr)
    raise SystemExit(1)
if non_error_events == 0:
    print("[capture-codex-golden] no non-error events found in rollout.jsonl", file=sys.stderr)
    raise SystemExit(1)
PY

CONVERTER_ARGS=()
if [[ "${ALLOW_EMPTY_TOOLS}" == "true" ]]; then
  CONVERTER_ARGS+=(--allow-empty-tools)
fi
"${PYTHON_BIN}" "${ROOT_DIR}/scripts/convert_codex_rollout_to_replay_session.py" \
  --input "${RUN_DIR}/rollout.jsonl" \
  --output "${RUN_DIR}/exports/replay_session.json" \
  --fallback-user-prompt "${PROMPT}" \
  ${CONVERTER_ARGS[@]+"${CONVERTER_ARGS[@]}"} \
  >>"${RUN_DIR}/stderr.txt" 2>&1

"${PYTHON_BIN}" - "${RUN_DIR}/exports/replay_session.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    print("[capture-codex-golden] replay_session.json missing", file=sys.stderr)
    raise SystemExit(1)

try:
    messages = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"[capture-codex-golden] replay_session.json is invalid JSON: {exc}", file=sys.stderr)
    raise SystemExit(1)

if not isinstance(messages, list):
    print("[capture-codex-golden] replay_session.json is not a message list", file=sys.stderr)
    raise SystemExit(1)

roles = [message.get("role") for message in messages if isinstance(message, dict)]
if len(messages) < 2 or "user" not in roles or "assistant" not in roles:
    print("[capture-codex-golden] replay_session.json must contain at least one user message and one assistant message", file=sys.stderr)
    raise SystemExit(1)
PY

if [[ -e "${SCEN_BASE_DIR}/current" && ! -L "${SCEN_BASE_DIR}/current" ]]; then
  rm -rf "${SCEN_BASE_DIR}/current"
fi
ln -sfn "${RUN_DIR}" "${SCEN_BASE_DIR}/current"
echo "[capture-codex-golden] Done: ${RUN_DIR}"
