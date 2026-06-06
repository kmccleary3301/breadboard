#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd -- "$ROOT_DIR/.." && pwd)"
cd "$ROOT_DIR"

STAMP="${P16_PHASE_I_STAMP:-$(date +%Y%m%d-%H%M%S)}"
ARTIFACT_ROOT="${P16_PHASE_I_ARTIFACT_ROOT:-$REPO_ROOT/docs_tmp/cli_phase_6/CODESIGN_p16/implementation_validation_p16_final_design_complete/artifacts/performance_accessibility_config/phase_i_${STAMP}}"

rm -rf "$ARTIFACT_ROOT"
mkdir -p "$ARTIFACT_ROOT"

pnpm scenario:batch scenarios/p16/batches/performance_long_session.json --artifact-root "$ARTIFACT_ROOT/performance_long_session" | tee "$ARTIFACT_ROOT/performance_long_session.log"
pnpm scenario:batch scenarios/p16/batches/accessibility_keyboard.json --artifact-root "$ARTIFACT_ROOT/accessibility_keyboard" | tee "$ARTIFACT_ROOT/accessibility_keyboard.log"
pnpm scenario:batch scenarios/p16/batches/config_conformance_final.json --artifact-root "$ARTIFACT_ROOT/config_conformance_final" | tee "$ARTIFACT_ROOT/config_conformance_final.log"
pnpm run runtime:validate:ascii-no-color | tee "$ARTIFACT_ROOT/ascii_no_color.log"
pnpm exec tsx scripts/p16_phase_i_config_accessibility_gate.ts "$ARTIFACT_ROOT" | tee "$ARTIFACT_ROOT/phase_i_gate.log"

cat > "$ARTIFACT_ROOT/phase_i_manifest.json" <<JSON
{
  "schemaVersion": "bb.p16.phase_i.all.v1",
  "createdAt": "$(date -Is)",
  "artifactRoot": "$ARTIFACT_ROOT",
  "performanceBatch": "$ARTIFACT_ROOT/performance_long_session/batch_manifest.json",
  "accessibilityBatch": "$ARTIFACT_ROOT/accessibility_keyboard/batch_manifest.json",
  "configBatch": "$ARTIFACT_ROOT/config_conformance_final/batch_manifest.json",
  "gateReport": "$ARTIFACT_ROOT/p16_phase_i_config_accessibility_gate.json"
}
JSON

echo "[p16][phase-i] artifacts $ARTIFACT_ROOT"
