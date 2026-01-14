#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${ROOT}/artifacts"
mkdir -p "${OUT_DIR}"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${OUT_DIR}/opentui_slab_${TS}.ansi"

# Run under a PTY so OpenTUI can read stdout.rows/columns (splitHeight needs real TTY geometry).
# Capture raw ANSI output for later review.
#
# Notes:
# - `timeout` ensures the capture ends without manual Ctrl+C.
# - `script` writes a typescript file; `-q` reduces extra headers, but your system may still add some metadata.

set +e
timeout 10s script -q -c "cd '${ROOT}' && bun run index.ts" "${OUT}"
CODE=$?
set -e

echo "Wrote: ${OUT}"
echo "Exit code: ${CODE} (124 means timeout)"
