#!/usr/bin/env bash
set -euo pipefail

MANIFEST_PATH="${1:-../misc/tui_goldens/manifests/tui_goldens.yaml}"
OUTPUT_ROOT="${2:-../misc/tui_goldens/artifacts}"
BLESSED_ROOT="${3:-../misc/tui_goldens/scenarios}"

node --import tsx scripts/run_tui_goldens.ts --manifest "$MANIFEST_PATH" --out "$OUTPUT_ROOT"

LATEST_RUN="$(ls -td "$OUTPUT_ROOT"/run-* | head -n 1)"
echo "[tui_goldens] latest run: $LATEST_RUN"

if ! ls "$LATEST_RUN"/*/render.txt >/dev/null 2>&1; then
  echo "[tui_goldens] no render.txt outputs found in $LATEST_RUN"
  exit 1
fi

if node --import tsx scripts/compare_tui_goldens.ts --manifest "$MANIFEST_PATH" --candidate "$LATEST_RUN" --blessed-root "$BLESSED_ROOT" --summary; then
  echo "[tui_goldens] compare ok"
else
  echo "[tui_goldens] compare failed (report-only)"
fi

if node --import tsx scripts/compare_tui_goldens_grid.ts --manifest "$MANIFEST_PATH" --candidate "$LATEST_RUN" --blessed-root "$BLESSED_ROOT" --summary; then
  echo "[tui_goldens] grid compare ok"
else
  echo "[tui_goldens] grid compare failed (report-only)"
fi
