#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${ROOT_DIR}/tools/claude_code_logged/dist"
VENDOR_DIR="${ROOT_DIR}/industry_coder_refs/claude_code_cli/node_modules/@anthropic-ai/claude-code"

if [[ ! -d "${DIST_DIR}" ]]; then
  echo "[claude-code-logged] dist/ missing, rebuilding from ${VENDOR_DIR}"
  node "${ROOT_DIR}/tools/claude_code_logged/scripts/build.mjs" --vendor "${VENDOR_DIR}"
fi

export PATH="${DIST_DIR}:${PATH}"
export CLAUDE_CODE_LOG_DIR="${CLAUDE_CODE_LOG_DIR:-${ROOT_DIR}/misc/claude_code_runs/logged_protofs/provider_dumps}"
export CLAUDE_CODE_WORKSPACE="${CLAUDE_CODE_WORKSPACE:-${ROOT_DIR}/misc/claude_code_runs/logged_protofs/workspace}"
export CLAUDE_CODE_HOME="${CLAUDE_CODE_HOME:-${ROOT_DIR}/misc/claude_code_runs/logged_protofs/home}"
export HOME="${CLAUDE_CODE_HOME}"

mkdir -p "${CLAUDE_CODE_LOG_DIR}"
mkdir -p "${CLAUDE_CODE_WORKSPACE}"
mkdir -p "${CLAUDE_CODE_HOME}"

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "[claude-code-logged] warning: ANTHROPIC_API_KEY is not set; requests will fail until you export a valid key." >&2
fi

exec claude-code-logged "$@"
