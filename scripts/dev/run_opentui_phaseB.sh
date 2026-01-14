#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

CONFIG_PATH="${1:-agent_configs/codex_cli_gpt51mini_e4_live.yaml}"
shift || true

export BREADBOARD_ENGINE_PREFER_BUNDLE=0
export BREADBOARD_CONFIG_PATH="${BREADBOARD_CONFIG_PATH:-$CONFIG_PATH}"

cd "${ROOT}/opentui_slab"
exec bun run phaseB/controller.ts --config "${BREADBOARD_CONFIG_PATH}" "$@"

