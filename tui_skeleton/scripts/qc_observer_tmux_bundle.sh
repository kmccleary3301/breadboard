#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <tmux-target> [extra run_stress_bundles args...]" >&2
  exit 2
fi

TARGET="$1"
shift

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ $# -eq 0 ]]; then
  set -- --case live_wrapper_plain_smoke
fi

exec pnpm exec tsx scripts/run_stress_bundles.ts \
  --include-live \
  --auto-start-live \
  --tmux-capture-target "$TARGET" \
  "$@"
