#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

CONFIG_PATH="${1:-agent_configs/test_enhanced_agent_v2.yaml}"
shift || true

export BREADBOARD_ENGINE_PREFER_BUNDLE=0
export BREADBOARD_CONFIG_PATH="${BREADBOARD_CONFIG_PATH:-$CONFIG_PATH}"

cd "${ROOT}/opentui_slab"
exec bun run index.ts --config "${BREADBOARD_CONFIG_PATH}" "$@"

