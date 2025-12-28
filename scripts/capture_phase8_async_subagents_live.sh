#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/capture_phase8_async_subagents_live.sh [options]

Options:
  --config <path>         Breadboard config YAML (default: agent_configs/claude_code_haiku45_phase8_async_live.yaml)
  --fixture-dir <path>    Workspace fixture dir to seed (default: misc/phase_8_fixtures/phase8_async_subagents_v1)
  --scenario <name>       Output scenario name under misc/phase_8_live_checks (default: claude_code_phase8_async_subagents_v1)
  --run-id <id>           Run id (default: UTC timestamp)
  --max-iterations <n>    Agent loop max iterations (default: 20)
  --prompt-source <path>  Replay session JSON containing the prompt (default: misc/claude_code_runs/replay_sessions/2.0.76/phase8_async_subagents_v1/replay_session.json)

Notes:
  - Writes to: misc/phase_8_live_checks/breadboard/<scenario>/<run-id>/
  - Seeds the workspace fixture and runs Breadboard in live mode (provider calls enabled).
  - Sources .env if present for API keys.
EOF
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CONFIG="${ROOT_DIR}/agent_configs/claude_code_haiku45_phase8_async_live.yaml"
FIXTURE_DIR="${ROOT_DIR}/misc/phase_8_fixtures/phase8_async_subagents_v1"
SCENARIO="claude_code_phase8_async_subagents_v1"
RUN_ID=""
MAX_ITERATIONS="20"
PROMPT_SOURCE="${ROOT_DIR}/misc/claude_code_runs/replay_sessions/2.0.76/phase8_async_subagents_v1/replay_session.json"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG="${2:-}"; shift 2 ;;
    --fixture-dir)
      FIXTURE_DIR="${2:-}"; shift 2 ;;
    --scenario)
      SCENARIO="${2:-}"; shift 2 ;;
    --run-id)
      RUN_ID="${2:-}"; shift 2 ;;
    --max-iterations)
      MAX_ITERATIONS="${2:-}"; shift 2 ;;
    --prompt-source)
      PROMPT_SOURCE="${2:-}"; shift 2 ;;
    --help|-h)
      usage; exit 0 ;;
    *)
      echo "[phase8] Unknown argument: $1" >&2
      usage
      exit 2 ;;
  esac
done

RUN_ID="${RUN_ID:-$(date -u +"%Y%m%d_%H%M%S")}"
RUN_DIR="${ROOT_DIR}/misc/phase_8_live_checks/breadboard/${SCENARIO}/${RUN_ID}"
WS_DIR="${RUN_DIR}/workspace"
RESULT_JSON="${RUN_DIR}/result.json"

mkdir -p "${RUN_DIR}"
rm -rf "${WS_DIR}"
mkdir -p "${WS_DIR}"

if [[ ! -d "${FIXTURE_DIR}" ]]; then
  echo "[phase8] fixture dir not found: ${FIXTURE_DIR}" >&2
  exit 2
fi
cp -a "${FIXTURE_DIR}/." "${WS_DIR}/"

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi

PROMPT="$(python - <<PY
import json
from pathlib import Path

src = Path(r'''${PROMPT_SOURCE}''')
entries = json.loads(src.read_text(encoding="utf-8"))
for e in entries:
    if isinstance(e, dict) and e.get("role") == "user":
        for part in e.get("parts") or []:
            if isinstance(part, dict) and part.get("type") == "text":
                print(part.get("text") or "")
                raise SystemExit(0)
raise SystemExit("no user prompt found in prompt source")
PY
)"

export PRESERVE_SEEDED_WORKSPACE=1
export RAY_SCE_SKIP_LSP=1

python "${ROOT_DIR}/main.py" "${CONFIG}" \
  --workspace "${WS_DIR}" \
  --task "${PROMPT}" \
  --max-iterations "${MAX_ITERATIONS}" \
  --result-json "${RESULT_JSON}"

echo "[phase8] Live run complete: ${RUN_DIR}"
