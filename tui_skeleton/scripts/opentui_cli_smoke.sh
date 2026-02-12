#!/usr/bin/env bash
set -euo pipefail

# CI-safe smoke: only checks CLI parsing (no engine start).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pushd "${ROOT_DIR}" >/dev/null

REPL_HELP="$(mktemp)"
UI_HELP="$(mktemp)"
cleanup() {
  rm -f "${REPL_HELP}" "${UI_HELP}"
}
trap cleanup EXIT

node node_modules/tsx/dist/cli.mjs src/main.ts repl -h >"${REPL_HELP}"
node node_modules/tsx/dist/cli.mjs src/main.ts ui -h >"${UI_HELP}"

grep -q -- "--tui" "${REPL_HELP}"
grep -q -- "--tui" "${UI_HELP}"

popd >/dev/null

echo "[opentui-cli-smoke] ok"

