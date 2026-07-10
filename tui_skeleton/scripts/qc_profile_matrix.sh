#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PROFILE="${1:-}"
shift || true

if [[ -z "$PROFILE" ]]; then
  cat >&2 <<USAGE
usage: scripts/qc_profile_matrix.sh <profile> [extra run_stress_bundles args...]

profiles:
  contract-fast
  interactive-deterministic
  live-fresh-wrapper
  observer-tmux <tmux-target>
  observer-emulator
  observer-emulator-resize
  observer-emulator-startup-smallheight
  maintenance-baseline
  p3-workloop-acceptance
  p4-wezterm-programmable
  p4-ghostty-visual
  p5-owned-live-prototype
  p5-owned-live-ghostty
  p6-escalated-owned-prototype
  p7-scene-owned-wezterm
  p8-scrollback-wezterm-core
  p8-scrollback-ghostty-mirror
  p10-scrollback-ghostty-native
  p11-scrollback-ghostty-native-closure
  stress-resize-stream
  legacy-wrapper-smoke
  release-candidate
USAGE
  exit 2
fi

run_cases() {
  local out_dir="$1"
  shift
  pnpm exec tsx scripts/run_stress_bundles.ts --no-zip --out-dir "$out_dir" "$@"
}

latest_run_dir() {
  local default_out_dir="$1"
  shift
  local out_dir="$default_out_dir"
  while [[ "$#" -gt 0 ]]; do
    case "$1" in
      --out-dir)
        if [[ "$#" -lt 2 ]]; then
          echo "--out-dir requires a value" >&2
          exit 2
        fi
        out_dir="$2"
        shift 2
        ;;
      --out-dir=*)
        out_dir="${1#--out-dir=}"
        shift
        ;;
      *)
        shift
        ;;
    esac
  done
  ls -td "$out_dir"/* | head -1
}

case "$PROFILE" in
  contract-fast)
    run_cases artifacts/qc_profile_contract_fast \
      --bundle-kind contract-fast \
      --bundle-authority canonical \
      --case spine_streaming_smoke \
      --case spine_mermaid_fallback \
      "$@"
    ;;
  interactive-deterministic)
    run_cases artifacts/qc_profile_interactive_deterministic \
      --bundle-kind interactive-deterministic \
      --bundle-authority canonical \
      --case spine_streaming_smoke \
      --case streaming_markdown \
      --case follow_pause_resume \
      --case file_picker \
      --case model_picker \
      "$@"
    ;;
  live-fresh-wrapper)
    run_cases artifacts/qc_profile_live_fresh_wrapper \
      --bundle-kind live-fresh-wrapper \
      --bundle-authority canonical \
      --fresh-live \
      --auto-start-live \
      --case live_wrapper_plain_smoke \
      --case live_wrapper_startup_smallheight \
      --case live_wrapper_multiturn_ordering \
      "$@"
    ;;
  observer-tmux)
    TARGET="${1:-}"
    if [[ -z "$TARGET" ]]; then
      echo "observer-tmux requires a tmux target" >&2
      exit 2
    fi
    shift
    run_cases artifacts/qc_profile_observer_tmux \
      --bundle-kind observer-tmux \
      --bundle-authority observer \
      --fresh-live \
      --auto-start-live \
      --live-config agent_configs/misc/opencode_mock_c_fs.yaml \
      --case maintenance_wrapper_plain_smoke \
      --tmux-capture-target "$TARGET" \
      "$@"
    ;;
  observer-emulator)
    run_cases artifacts/qc_profile_observer_emulator \
      --bundle-kind observer-emulator \
      --bundle-authority emulator \
      --fresh-live \
      --auto-start-live \
      --case live_wrapper_emulator_plain_smoke \
      "$@"
    ;;
  observer-emulator-resize)
    run_cases artifacts/qc_profile_observer_emulator_resize \
      --bundle-kind observer-emulator-resize \
      --bundle-authority emulator \
      --fresh-live \
      --auto-start-live \
      --case live_wrapper_emulator_plain_smoke \
      --case live_wrapper_emulator_resize_smoke \
      "$@"
    ;;
  observer-emulator-startup-smallheight)
    run_cases artifacts/qc_profile_observer_emulator_startup_smallheight \
      --bundle-kind observer-emulator-startup-smallheight \
      --bundle-authority emulator \
      --fresh-live \
      --auto-start-live \
      --case live_wrapper_emulator_startup_smallheight \
      "$@"
    ;;
  maintenance-baseline)
    run_cases artifacts/qc_profile_maintenance_baseline \
      --bundle-kind maintenance-baseline \
      --bundle-authority maintenance \
      --fresh-live \
      --auto-start-live \
      --live-config agent_configs/misc/opencode_mock_c_fs.yaml \
      --case spine_streaming_smoke \
      --case spine_mermaid_fallback \
      --case maintenance_wrapper_plain_smoke \
      --case maintenance_wrapper_multiturn_ordering \
      --case maintenance_wrapper_tool_smoke \
      --case maintenance_wrapper_resize_spacer_regression \
      --case maintenance_wrapper_compact_session_header \
      --case maintenance_wrapper_markdown_projection \
      --case maintenance_wrapper_emulator_plain_smoke \
      --case maintenance_wrapper_emulator_resize_smoke \
      --case maintenance_wrapper_emulator_startup_smallheight \
      "$@"
    ;;
  p3-workloop-acceptance)
    run_cases artifacts/qc_profile_p3_workloop_acceptance \
      --bundle-kind p3-workloop-acceptance \
      --bundle-authority canonical \
      --case recent_sessions_overlay \
      --case transcript_viewer_navigation \
      --case transcript_result_actionability \
      --case collapsed_detail_inspection \
      --case composer_continuation_cues \
      --case composer_command_surface \
      "$@"
    ;;
  p4-wezterm-programmable)
    run_cases artifacts/qc_profile_p4_wezterm_programmable \
      --bundle-kind p4-wezterm-programmable \
      --bundle-authority canonical \
      --fresh-live \
      --auto-start-live \
      --case p4_wezterm_streaming_smoke \
      --case p4_wezterm_multiturn_width_shrink \
      "$@"
    ;;
  p4-ghostty-visual)
    run_cases artifacts/qc_profile_p4_ghostty_visual \
      --bundle-kind p4-ghostty-visual \
      --bundle-authority canonical \
      --case p4_ghostty_launch_stream_probe \
      "$@"
    ;;
  p4-ghostty-human-truth)
    run_cases artifacts/qc_profile_p4_ghostty_human_truth \
      --bundle-kind p4-ghostty-human-truth \
      --bundle-authority canonical \
      --case p4_ghostty_streaming_current_window \
      --case p4_ghostty_multiturn_width_shrink \
      "$@"
    ;;
  p5-owned-live-prototype)
    run_cases artifacts/qc_profile_p5_owned_live_prototype       --bundle-kind p5-owned-live-prototype       --bundle-authority canonical       --fresh-live       --auto-start-live       --case p5_wezterm_owned_live_streaming_smoke       --case p5_wezterm_owned_live_transcript_navigation       --case p5_wezterm_owned_live_multiturn_width_shrink       --case p5_wezterm_owned_live_height_change       --case p5_wezterm_owned_live_network_error       "$@"
    ;;
  p5-owned-live-ghostty)
    run_cases artifacts/qc_profile_p5_owned_live_ghostty       --bundle-kind p5-owned-live-ghostty       --bundle-authority canonical       --fresh-live       --auto-start-live       --case p5_ghostty_owned_live_streaming_current_window       --case p5_ghostty_owned_live_multiturn_width_shrink       "$@"
    ;;
  p6-escalated-owned-prototype)
    run_cases artifacts/qc_profile_p6_escalated_owned_prototype       --bundle-kind p6-escalated-owned-prototype       --bundle-authority canonical       --fresh-live       --auto-start-live       --case p6_wezterm_escalated_streaming_smoke       --case p6_wezterm_escalated_transcript_navigation       --case p6_wezterm_escalated_multiturn_width_shrink       --case p6_wezterm_escalated_network_error       "$@"
    ;;
  p8-scrollback-ghostty-mirror)
    run_cases artifacts/qc_profile_p8_scrollback_ghostty_mirror       --bundle-kind p8-scrollback-ghostty-mirror       --bundle-authority canonical       --fresh-live       --auto-start-live       --case p8_ghostty_scrollback_seeded_host_history_guard       --case p8_ghostty_scrollback_multiturn_width_shrink       "$@"
    ;;
  p10-scrollback-ghostty-native)
    p10_out_dir="artifacts/qc_profile_p10_scrollback_ghostty_native"
    run_cases "$p10_out_dir"       --bundle-kind p10-scrollback-ghostty-native       --bundle-authority canonical       --fresh-live       --auto-start-live       --case p10_ghostty_native_scrollback_seeded_host_history_guard       --case p10_ghostty_native_scrollback_width_shrink       --case p10_ghostty_native_scrollback_multiturn_width_shrink       --case p10_ghostty_native_scrollback_height_change       --case p10_ghostty_native_scrollback_resize_churn       "$@"
    latest_p10="$(latest_run_dir "$p10_out_dir" "$@")"
    node --import tsx tools/assertions/northStarScrollbackAudit.ts "$latest_p10"
    ;;
  p11-scrollback-ghostty-native-closure)
    p11_out_dir="artifacts/qc_profile_p11_scrollback_ghostty_native_closure"
    run_cases "$p11_out_dir" \
      --bundle-kind p11-scrollback-ghostty-native-closure \
      --bundle-authority canonical \
      --fresh-live \
      --auto-start-live \
      --case p11_ghostty_native_scrollback_history_guard_v2 \
      --case p11_ghostty_native_scrollback_width_equiv_structured_wide \
      --case p11_ghostty_native_scrollback_width_equiv_structured_narrow \
      --case p11_ghostty_native_scrollback_multiturn_width_shrink \
      --case p11_ghostty_native_scrollback_height_change \
      --case p11_ghostty_native_scrollback_resize_churn \
      "$@"
    latest_p11="$(latest_run_dir "$p11_out_dir" "$@")"
    node --import tsx tools/assertions/northStarScrollbackAudit.ts "$latest_p11"
    node --import tsx tools/assertions/scrollbackClauseCompletenessCheck.ts --root "$latest_p11"
    cat > "$latest_p11/p11_width_equivalence_structured_pair.json" <<JSON
{
  "id": "p11_width_equivalence_structured",
  "scope": "structured-block",
  "pathA": {
    "caseDir": "$latest_p11/p11_ghostty_native_scrollback_width_equiv_structured_wide",
    "checkpoint": "after-width-shrink"
  },
  "pathB": {
    "caseDir": "$latest_p11/p11_ghostty_native_scrollback_width_equiv_structured_narrow",
    "checkpoint": "turn1-settled"
  }
}
JSON
    node --import tsx tools/assertions/scrollbackWidthEquivalenceCheck.ts --pair-config "$latest_p11/p11_width_equivalence_structured_pair.json" \
      > "$latest_p11/p11_width_equivalence_structured_report.json"
    node --import tsx tools/assertions/scrollbackFullHistoryDiffCheck.ts \
      --case-dir "$latest_p11/p11_ghostty_native_scrollback_history_guard_v2" \
      --config scripts/p11_full_history_rich_sentinels_config.json \
      > "$latest_p11/p11_full_history_rich_sentinels_report.json"
    ;;
  p8-scrollback-wezterm-core)
    p8_out_dir="artifacts/qc_profile_p8_scrollback_wezterm_core"
    run_cases "$p8_out_dir"       --bundle-kind p8-scrollback-wezterm-core       --bundle-authority canonical       --fresh-live       --auto-start-live       --case p8_wezterm_scrollback_seeded_host_history_guard       --case p8_wezterm_scrollback_multiturn_width_shrink       "$@"
    latest_p8="$(latest_run_dir "$p8_out_dir" "$@")"
    node --import tsx tools/assertions/northStarScrollbackAudit.ts "$latest_p8"
    ;;
  p7-scene-owned-wezterm)
    run_cases artifacts/qc_profile_p7_scene_owned_wezterm       --bundle-kind p7-scene-owned-wezterm       --bundle-authority canonical       --fresh-live       --auto-start-live       --case p7_wezterm_scene_owned_streaming_smoke       --case p7_wezterm_scene_owned_transcript_navigation       --case p7_wezterm_scene_owned_multiturn_width_shrink       "$@"
    ;;
  p7-scene-owned-ghostty)
    run_cases artifacts/qc_profile_p7_scene_owned_ghostty       --bundle-kind p7-scene-owned-ghostty       --bundle-authority canonical       --fresh-live       --auto-start-live       --case p7_ghostty_scene_owned_streaming_current_window       --case p7_ghostty_scene_owned_multiturn_width_shrink       "$@"
    ;;
  p7-scene-owned-wezterm-credibility)
    run_cases artifacts/qc_profile_p7_scene_owned_wezterm_credibility       --bundle-kind p7-scene-owned-wezterm-credibility       --bundle-authority canonical       --fresh-live       --auto-start-live       --case p7_wezterm_scene_owned_height_change       --case p7_wezterm_scene_owned_network_error       --case p7_wezterm_scene_owned_streaming_resize_churn       "$@"
    ;;
  p7-scene-owned-ghostty-credibility)
    run_cases artifacts/qc_profile_p7_scene_owned_ghostty_credibility       --bundle-kind p7-scene-owned-ghostty-credibility       --bundle-authority canonical       --fresh-live       --auto-start-live       --case p7_ghostty_scene_owned_height_change       --case p7_ghostty_scene_owned_network_error       --case p7_ghostty_scene_owned_streaming_resize_churn       "$@"
    ;;
  p7-scene-owned-wezterm-acceptance)
    run_cases artifacts/qc_profile_p7_scene_owned_wezterm_acceptance       --bundle-kind p7-scene-owned-wezterm-acceptance       --bundle-authority canonical       --fresh-live       --auto-start-live       --case p7_wezterm_scene_owned_streaming_smoke       --case p7_wezterm_scene_owned_transcript_navigation       --case p7_wezterm_scene_owned_multiturn_width_shrink       --case p7_wezterm_scene_owned_height_change       --case p7_wezterm_scene_owned_network_error       --case p7_wezterm_scene_owned_streaming_resize_churn       "$@"
    ;;
  p7-scene-owned-ghostty-acceptance)
    run_cases artifacts/qc_profile_p7_scene_owned_ghostty_acceptance       --bundle-kind p7-scene-owned-ghostty-acceptance       --bundle-authority canonical       --fresh-live       --auto-start-live       --case p7_ghostty_scene_owned_streaming_current_window       --case p7_ghostty_scene_owned_multiturn_width_shrink       --case p7_ghostty_scene_owned_height_change       --case p7_ghostty_scene_owned_network_error       --case p7_ghostty_scene_owned_streaming_resize_churn       "$@"
    ;;
  p7-default-flagship-ceremonial)
    run_cases artifacts/qc_profile_p7_default_flagship_ceremonial       --bundle-kind p7-default-flagship-ceremonial       --bundle-authority canonical       --fresh-live       --auto-start-live       --case p7_wezterm_default_flagship_smoke       --case p7_ghostty_default_flagship_smoke       "$@"
    ;;
  p6-escalated-owned-ghostty)
    run_cases artifacts/qc_profile_p6_escalated_owned_ghostty       --bundle-kind p6-escalated-owned-ghostty       --bundle-authority canonical       --fresh-live       --auto-start-live       --case p6_ghostty_escalated_streaming_current_window       --case p6_ghostty_escalated_multiturn_width_shrink       "$@"
    ;;
  stress-resize-stream)
    run_cases artifacts/qc_profile_stress_resize_stream \
      --bundle-kind stress-resize-stream \
      --bundle-authority canonical \
      --fresh-live \
      --auto-start-live \
      --case live_wrapper_resize_spacer_regression \
      "$@"
    ;;
  legacy-wrapper-smoke)
    run_cases artifacts/qc_profile_legacy_wrapper_smoke \
      --bundle-kind legacy-wrapper-smoke \
      --bundle-authority maintenance \
      --fresh-live \
      --auto-start-live \
      --case spine_streaming_smoke \
      --case spine_mermaid_fallback \
      --case live_wrapper_plain_smoke \
      --case live_wrapper_startup_smallheight \
      --case live_wrapper_multiturn_ordering \
      --case live_wrapper_resize_spacer_regression \
      "$@"
    ;;
  release-candidate)
    bash scripts/qc_scrollback_control_sequences.sh
    run_cases artifacts/qc_profile_release_candidate \
      --bundle-kind release-candidate \
      --bundle-authority canonical \
      --fresh-live \
      --auto-start-live \
      --case p8_wezterm_scrollback_seeded_host_history_guard \
      --case p8_wezterm_scrollback_multiturn_width_shrink \
      --case p10_ghostty_native_scrollback_seeded_host_history_guard \
      --case p10_ghostty_native_scrollback_width_shrink \
      --case p10_ghostty_native_scrollback_multiturn_width_shrink \
      --case p10_ghostty_native_scrollback_height_change \
      --case p10_ghostty_native_scrollback_resize_churn \
      "$@"
    latest_release="$(ls -td artifacts/qc_profile_release_candidate/* | head -1)"
    node --import tsx tools/assertions/northStarScrollbackAudit.ts "$latest_release"
    ;;
  *)
    echo "unknown profile: $PROFILE" >&2
    exit 2
    ;;
esac
