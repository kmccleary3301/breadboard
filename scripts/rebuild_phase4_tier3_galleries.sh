#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCENARIO_ROOT="${1:-$ROOT_DIR/docs_tmp/tmux_captures/scenarios}"

gen_latest_gallery() {
  local lane_dir="$1"
  local manifest
  manifest="$(find "$lane_dir" -name scenario_manifest.json -print 2>/dev/null | sort | tail -n1 || true)"
  if [[ -z "$manifest" ]]; then
    echo "[phase4-tier3-galleries] missing lane dir: $lane_dir"
    return 0
  fi
  local run_dir
  run_dir="$(dirname "$manifest")"
  python "$ROOT_DIR/scripts/generate_tmux_run_gallery.py" --run-dir "$run_dir" --output-html "$run_dir/gallery.html"
}

gen_latest_gallery "$SCENARIO_ROOT/phase4_replay/resize_storm_overlay_v1"
gen_latest_gallery "$SCENARIO_ROOT/phase4_replay/subagents_concurrency_20_v1"
gen_latest_gallery "$SCENARIO_ROOT/nightly_provider/codex_e2e_compact_semantic_v1"
gen_latest_gallery "$SCENARIO_ROOT/nightly_provider/claude_e2e_compact_semantic_v1"

python "$ROOT_DIR/scripts/build_phase4_tier3_gallery_index.py" \
  --scenarios-root "$SCENARIO_ROOT" \
  --output-md "$ROOT_DIR/docs_tmp/tmux_captures/phase4_tier3_gallery_index.md" \
  --output-json "$ROOT_DIR/docs_tmp/tmux_captures/phase4_tier3_gallery_index.json"

echo "[phase4-tier3-galleries] complete"
