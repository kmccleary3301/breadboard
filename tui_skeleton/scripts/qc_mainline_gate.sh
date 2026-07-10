#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SESSION_NAME="bbqc-mainline-$$"
cleanup() {
  tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true
}
trap cleanup EXIT

printf '[qc:gate:mainline] interactive-deterministic
'
npm run -s qc:profile:interactive-deterministic

printf '[qc:gate:mainline] maintenance-baseline
'
npm run -s qc:profile:maintenance-baseline

printf '[qc:gate:mainline] observer-tmux
'
tmux new-session -d -s "$SESSION_NAME"
npm run -s qc:profile:observer-tmux -- "$SESSION_NAME:0.0"
