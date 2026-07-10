#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"
OUT_ROOT="${P16_HOST_OUT_ROOT:-$REPO_ROOT/docs_tmp/cli_phase_6/CODESIGN_p16/implementation_validation_p16_final_design_complete/artifacts/host/ghostty}"
OUT_ROOT="$(readlink -m "$OUT_ROOT")"
STAMP="${P16_HOST_STAMP:-$(date +%Y%m%d-%H%M%S)}"
RUN_DIR="$OUT_ROOT/ghostty_supported_lane_$STAMP"
mkdir -p "$RUN_DIR"
cd "$ROOT_DIR"

log_run() {
  local name="$1"
  shift
  echo "[p16:ghostty] $name" | tee -a "$RUN_DIR/commands.log"
  printf '%q ' "$@" >> "$RUN_DIR/commands.log"
  printf '\n' >> "$RUN_DIR/commands.log"
  "$@" 2>&1 | tee "$RUN_DIR/${name}.log"
}

log_run preflight pnpm run qc:preflight:ghostty-native-export
log_run p10 bash scripts/qc_profile_matrix.sh p10-scrollback-ghostty-native --out-dir "$RUN_DIR/p10_native"
log_run p7_acceptance bash scripts/qc_profile_matrix.sh p7-scene-owned-ghostty-acceptance --out-dir "$RUN_DIR/p7_scene_owned_acceptance"

cat > "$RUN_DIR/manifest.json" <<JSON
{
  "schemaVersion": 1,
  "id": "p16_ghostty_supported_lane",
  "createdAt": "$(date -Iseconds)",
  "ok": true,
  "outRoot": "$OUT_ROOT",
  "runDir": "$RUN_DIR",
  "evidenceModel": "Ghostty native export preflight plus supported qc_profile_matrix Ghostty profiles",
  "commands": [
    "pnpm run qc:preflight:ghostty-native-export",
    "bash scripts/qc_profile_matrix.sh p10-scrollback-ghostty-native --out-dir $RUN_DIR/p10_native",
    "bash scripts/qc_profile_matrix.sh p7-scene-owned-ghostty-acceptance --out-dir $RUN_DIR/p7_scene_owned_acceptance"
  ]
}
JSON

echo "$RUN_DIR"
