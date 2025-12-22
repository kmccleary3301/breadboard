#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$ROOT_DIR/agent_configs/multi_agent_sync_main.yaml"
TASK="$ROOT_DIR/implementations/test_tasks/multi_agent_sync_subagent.md"
WORKSPACE="$ROOT_DIR/misc/multi_agent_runs/sync_subagent_workspace"
LOG_ROOT="$ROOT_DIR/logging"

mkdir -p "$WORKSPACE"

if [[ "${RUN_LIVE:-}" != "1" ]]; then
  echo "[phase8] Multi-agent sync golden capture (dry)."
  echo "Set RUN_LIVE=1 to execute a live run."
  echo "Command: python $ROOT_DIR/main.py $CONFIG --workspace $WORKSPACE --task $TASK"
  exit 0
fi

export PRESERVE_SEEDED_WORKSPACE=1
python "$ROOT_DIR/main.py" "$CONFIG" --workspace "$WORKSPACE" --task "$TASK"

# Capture latest logging directory for manual inspection.
LATEST_LOG=$(ls -1dt "$LOG_ROOT"/* 2>/dev/null | head -n 1 || true)
if [[ -n "$LATEST_LOG" ]]; then
  echo "[phase8] Latest run logged at: $LATEST_LOG"
fi
