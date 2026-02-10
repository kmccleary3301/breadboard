#!/usr/bin/env bash
set -euo pipefail

# Bootstraps fresh Codex + Claude tmux sessions (breadboard_test_* only), then runs
# the canonical reference scenarios and optionally compares against provided goldens.
#
# This avoids a major source of flakiness: reusing long-lived interactive sessions
# whose composer/input state may be contaminated by prior manual interaction.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOTSTRAP="${ROOT_DIR}/scripts/bootstrap_logged_sessions.py"
RUN_REFERENCE="${ROOT_DIR}/scripts/run_reference_tmux_scenarios.sh"

STAMP="$(date +%Y%m%d_%H%M%S)"
PREFIX="breadboard_test_ref_fresh_${STAMP}"
WORKSPACE="${ROOT_DIR}"
MODEL_ID="${CLAUDE_MODEL_ID:-claude-haiku-4-5-20251001}"
CODEX_GOLDEN_DIR="${TMUX_GOLDEN_CODEX:-}"
CLAUDE_GOLDEN_DIR="${TMUX_GOLDEN_CLAUDE:-}"
CLEANUP=0

usage() {
  cat <<'USAGE'
Usage:
  scripts/run_reference_tmux_scenarios_fresh.sh [options]

Options:
  --prefix <name>          tmux session prefix (default: breadboard_test_ref_fresh_<timestamp>)
  --workspace <path>       workspace path passed to providers (default: repo root)
  --claude-model <model>   pinned Claude model id (default: claude-haiku-4-5-20251001)
  --codex-golden <path>    codex golden dir to compare against (default: TMUX_GOLDEN_CODEX env)
  --claude-golden <path>   claude golden dir to compare against (default: TMUX_GOLDEN_CLAUDE env)
  --cleanup                kill the created tmux sessions after the run completes
  -h, --help               show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)
      PREFIX="${2:-}"
      shift 2
      ;;
    --workspace)
      WORKSPACE="${2:-}"
      shift 2
      ;;
    --claude-model|--model)
      MODEL_ID="${2:-}"
      shift 2
      ;;
    --codex-golden)
      CODEX_GOLDEN_DIR="${2:-}"
      shift 2
      ;;
    --claude-golden)
      CLAUDE_GOLDEN_DIR="${2:-}"
      shift 2
      ;;
    --cleanup)
      CLEANUP=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "${PREFIX}" ]]; then
  echo "prefix cannot be empty" >&2
  exit 2
fi
if [[ "${PREFIX}" != breadboard_test_* ]]; then
  PREFIX="breadboard_test_${PREFIX}"
fi

extract_json() {
  # Print a single JSON field given a dotted path like: targets.codex
  python -c '
import json, sys
path = sys.argv[1].split(".")
payload = json.loads(sys.stdin.read() or "{}")
cur = payload
for key in path:
    if not isinstance(cur, dict) or key not in cur:
        print("")
        raise SystemExit(0)
    cur = cur[key]
print(cur if isinstance(cur, str) else json.dumps(cur))
' "$1"
}

echo "[fresh-ref] bootstrapping Codex (${PREFIX}_codex / ${PREFIX}_proxy)"
CODEX_JSON="$(
  python "${BOOTSTRAP}" \
    --provider codex \
    --session "${PREFIX}_codex_stack" \
    --workspace "${WORKSPACE}" \
    --timeout 180 \
    --noninteractive \
    --strict
)"
CODEX_TARGET="$(printf '%s' "${CODEX_JSON}" | extract_json "targets.codex")"
if [[ -z "${CODEX_TARGET}" ]]; then
  echo "[fresh-ref] error: failed to bootstrap Codex" >&2
  echo "${CODEX_JSON}" >&2
  exit 3
fi

echo "[fresh-ref] bootstrapping Claude (${PREFIX}_claude)"
CLAUDE_JSON="$(
  python "${BOOTSTRAP}" \
    --provider claude \
    --session "${PREFIX}_claude" \
    --workspace "${WORKSPACE}" \
    --model "${MODEL_ID}" \
    --timeout 240 \
    --noninteractive \
    --strict
)"
CLAUDE_TARGET="$(printf '%s' "${CLAUDE_JSON}" | extract_json "targets.main")"
if [[ -z "${CLAUDE_TARGET}" ]]; then
  echo "[fresh-ref] error: failed to bootstrap Claude" >&2
  echo "${CLAUDE_JSON}" >&2
  exit 3
fi

echo "[fresh-ref] running scenarios"
set +e
bash "${RUN_REFERENCE}" "${CODEX_TARGET}" "${CLAUDE_TARGET}" "${CODEX_GOLDEN_DIR}" "${CLAUDE_GOLDEN_DIR}"
code=$?
set -e

if [[ "${CLEANUP}" == "1" ]]; then
  echo "[fresh-ref] cleaning up tmux sessions"
  # Codex bootstrap creates two sessions: <prefix>_codex_stack_proxy and <prefix>_codex_stack_codex
  tmux kill-session -t "${PREFIX}_codex_stack_proxy" 2>/dev/null || true
  tmux kill-session -t "${PREFIX}_codex_stack_codex" 2>/dev/null || true
  tmux kill-session -t "${PREFIX}_claude" 2>/dev/null || true
fi

exit "${code}"
