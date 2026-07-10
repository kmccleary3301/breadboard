#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TUI_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${TUI_ROOT}/.." && pwd)"
P16_ROOT="${REPO_ROOT}/docs_tmp/cli_phase_6/CODESIGN_p16/implementation_validation_p16_final_design_complete"
STAMP="${P16_HOST_STAMP:-$(date +%Y%m%d-%H%M%S)}"
ARTIFACT_ROOT="${P16_HOST_OUT_ROOT:-${P16_ROOT}/artifacts/host/wezterm/wezterm_slash_composer_${STAMP}}"
ARTIFACT_ROOT="$(readlink -m "${ARTIFACT_ROOT}")"

mkdir -p "${ARTIFACT_ROOT}"
cd "${TUI_ROOT}"

{
  echo "createdAt=$(date -Iseconds)"
  echo "pwd=$(pwd)"
  echo "gitHead=$(git rev-parse HEAD 2>/dev/null || true)"
  echo "gitStatusHash=$(git status --short | sha256sum | awk '{print $1}')"
  echo "whichBb=$(command -v bb || true)"
  echo "whichBreadboard=$(command -v breadboard || true)"
  echo "bbVersion=$(bb --version 2>/dev/null || true)"
  echo "breadboardVersion=$(breadboard --version 2>/dev/null || true)"
} >"${ARTIFACT_ROOT}/provenance.env"

pnpm scenario:batch scenarios/p16/batches/wezterm_slash_composer.json \
  --artifact-root "${ARTIFACT_ROOT}" \
  >"${ARTIFACT_ROOT}/scenario_batch.log" 2>&1

case_dir="$(find "${ARTIFACT_ROOT}" -mindepth 1 -maxdepth 1 -type d -name '*p16_host_wezterm_slash_composer_wezterm' | sort | tail -n 1)"
if [[ -z "${case_dir}" ]]; then
  echo "Could not find WezTerm slash/composer case directory under ${ARTIFACT_ROOT}" >&2
  exit 1
fi

node - "${case_dir}/terminal_frames.ndjson" "${ARTIFACT_ROOT}/wezterm_slash_composer_snapshots.txt" <<'NODE'
const fs = require("fs")
const [framesPath, outPath] = process.argv.slice(2)
const raw = fs.readFileSync(framesPath, "utf8").trim()
const frames = raw ? JSON.parse(raw) : []
const text = frames.map((frame) => `# ${frame.label}\n${frame.text ?? ""}`).join("\n\n")
fs.writeFileSync(outPath, `${text}\n`, "utf8")
NODE

P16_SLASH_GATE_REQUIRE_HISTORY=0 node --import tsx scripts/p16_core_ux_slash_composer_gate.ts \
  "${ARTIFACT_ROOT}/wezterm_slash_composer_snapshots.txt" \
  "${ARTIFACT_ROOT}/p16_wezterm_slash_composer_gate.json"

cat >"${ARTIFACT_ROOT}/host_slash_composer_manifest.json" <<JSON
{
  "schemaVersion": 1,
  "id": "p16_host_wezterm_slash_composer",
  "createdAt": "$(date -Iseconds)",
  "ok": true,
  "artifactRoot": "${ARTIFACT_ROOT}",
  "caseDir": "${case_dir}",
  "batchManifest": "${ARTIFACT_ROOT}/batch_manifest.json",
  "snapshotGate": "${ARTIFACT_ROOT}/p16_wezterm_slash_composer_gate.json",
  "scope": "Representative WezTerm host evidence for slash/composer rendering, resize, and stale-prompt absence. This does not prove Ghostty, VSCode, Cursor, or universal host behavior."
}
JSON

echo "${ARTIFACT_ROOT}"
