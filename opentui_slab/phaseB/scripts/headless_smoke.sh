#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

CONFIG="${1:-agent_configs/codex_0-107-0_e4_3-6-2026.yaml}"

timeout 25s bun run phaseB/controller.ts \
  --no-ui \
  --config "${CONFIG}" \
  --task "PhaseB headless smoke: say hi." \
  --exit-after-ms 15000

