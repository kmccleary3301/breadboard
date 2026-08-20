#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TUI_DIR="${ROOT_DIR}/tui_skeleton"

echo "[pack-smoke] building TUI"
pushd "${TUI_DIR}" >/dev/null
npm run build

echo "[pack-smoke] packing npm tarball"
TARBALL="$(npm pack --silent)"

TMPDIR="$(mktemp -d)"
tar -xzf "${TARBALL}" -C "${TMPDIR}"

echo "[pack-smoke] installing packed artifact"
npm install --silent --prefix "${TMPDIR}" "${TUI_DIR}/${TARBALL}"

echo "[pack-smoke] running CLI from installed artifact"
node "${TMPDIR}/node_modules/.bin/breadboard" --help >/dev/null

popd >/dev/null

rm -rf "${TMPDIR}" "${TUI_DIR:?}/${TARBALL}"
echo "[pack-smoke] ok"
