#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  cat <<'EOF'
Usage:
  scripts/run_phase5_qc_roundtrip.sh <out_dir> <stamp> [ground_truth_metrics_json]

Runs a replay-first Phase5 QC roundtrip:
1) fresh hard-gate recapture signoff (1 iteration)
2) curate/validate fullpane visual pack
3) generate footer QC pack
4) build visual drift metrics
5) rank discrepancies

Defaults:
- ground_truth_metrics_json:
  /shared_folders/querylake_server/ray_testing/ray_SCE/docs_tmp/cli_phase_5/ground_truth_eval/gt_frozen_replaycost_20260224a/comparison_metrics.json
EOF
  exit 2
fi

OUT_DIR="$1"
STAMP="$2"
GT_METRICS="${3:-/shared_folders/querylake_server/ray_testing/ray_SCE/docs_tmp/cli_phase_5/ground_truth_eval/gt_frozen_replaycost_20260224a/comparison_metrics.json}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

mkdir -p "$OUT_DIR"

HARD_SIGNOFF_JSON="$OUT_DIR/phase5_roundtrip_hard_signoff_${STAMP}.json"
HARD_SIGNOFF_MD="$OUT_DIR/phase5_roundtrip_hard_signoff_${STAMP}.md"
FLAT_BUNDLE_DIR="$OUT_DIR/phase5_roundtrip_flat_bundle_${STAMP}"
FLAT_BUNDLE_VALIDATION_JSON="$OUT_DIR/phase5_roundtrip_flat_bundle_${STAMP}_validation.json"
FOOTER_QC_DIR="$OUT_DIR/phase5_roundtrip_footer_qc_${STAMP}"
FOOTER_CONTRAST_JSON="$OUT_DIR/phase5_roundtrip_footer_contrast_gate_${STAMP}.json"
FOOTER_CONTRAST_MD="$OUT_DIR/phase5_roundtrip_footer_contrast_gate_${STAMP}.md"
VISUAL_METRICS_JSON="$OUT_DIR/phase5_roundtrip_visual_drift_metrics_${STAMP}.json"
RANKING_JSON="$OUT_DIR/phase5_roundtrip_discrepancy_ranking_${STAMP}.json"
RANKING_MD="$OUT_DIR/phase5_roundtrip_discrepancy_ranking_${STAMP}.md"
RANKING_GATE_JSON="$OUT_DIR/phase5_roundtrip_discrepancy_budget_gate_${STAMP}.json"
RANKING_GATE_MD="$OUT_DIR/phase5_roundtrip_discrepancy_budget_gate_${STAMP}.md"
POSTCERT_MONITOR_JSON="$OUT_DIR/phase5_roundtrip_postcert_monitoring_${STAMP}.json"
POSTCERT_MONITOR_MD="$OUT_DIR/phase5_roundtrip_postcert_monitoring_${STAMP}.md"
ADVANCED_JSON="$OUT_DIR/phase5_advanced_behavior_stress_${STAMP}.json"
ADVANCED_MD="$OUT_DIR/phase5_advanced_behavior_stress_${STAMP}.md"
RELIABILITY_JSON="$OUT_DIR/phase5_roundtrip_reliability_${STAMP}.json"
RELIABILITY_MD="$OUT_DIR/phase5_roundtrip_reliability_${STAMP}.md"
RELIABILITY_FLAKE_JSON="$OUT_DIR/phase5_roundtrip_reliability_flake_${STAMP}.json"
RELIABILITY_FLAKE_MD="$OUT_DIR/phase5_roundtrip_reliability_flake_${STAMP}.md"
RELIABILITY_FLAKE_GATE_JSON="$OUT_DIR/phase5_roundtrip_reliability_flake_budget_${STAMP}.json"
RELIABILITY_FLAKE_GATE_MD="$OUT_DIR/phase5_roundtrip_reliability_flake_budget_${STAMP}.md"
ARTIFACT_JSON="$OUT_DIR/phase5_roundtrip_artifact_integrity_${STAMP}.json"
ARTIFACT_MD="$OUT_DIR/phase5_roundtrip_artifact_integrity_${STAMP}.md"
LATENCY_JSON="$OUT_DIR/phase5_roundtrip_latency_trend_${STAMP}.json"
LATENCY_MD="$OUT_DIR/phase5_roundtrip_latency_trend_${STAMP}.md"
LATENCY_DRIFT_GATE_JSON="$OUT_DIR/phase5_roundtrip_latency_drift_gate_${STAMP}.json"
LATENCY_DRIFT_GATE_MD="$OUT_DIR/phase5_roundtrip_latency_drift_gate_${STAMP}.md"
CANARY_HOTSPOT_JSON="$OUT_DIR/phase5_roundtrip_canary_hotspots_${STAMP}.json"
CANARY_HOTSPOT_MD="$OUT_DIR/phase5_roundtrip_canary_hotspots_${STAMP}.md"
CANARY_TIMESERIES_JSON="$OUT_DIR/phase5_roundtrip_canary_timeseries_${STAMP}.json"
CANARY_TIMESERIES_MD="$OUT_DIR/phase5_roundtrip_canary_timeseries_${STAMP}.md"
CANARY_WAIT_GATE_JSON="$OUT_DIR/phase5_roundtrip_canary_wait_alignment_${STAMP}.json"
CANARY_WAIT_GATE_MD="$OUT_DIR/phase5_roundtrip_canary_wait_alignment_${STAMP}.md"
ALLOWLIST_JSON="config/text_contracts/phase5_discrepancy_allowlist_v1.json"
EXPECTED_WAITS_JSON="config/text_contracts/phase5_expected_wait_actions_v1.json"

NIGHTLY_SIGNOFF_JSON="$OUT_DIR/phase5_gemini_live_signoff_nightly_retry_20260223.json"
if [[ ! -f "$NIGHTLY_SIGNOFF_JSON" ]]; then
  NIGHTLY_SIGNOFF_JSON="docs_tmp/cli_phase_5/phase5_gemini_live_signoff_nightly_retry_20260223.json"
fi

if [[ ! -f "$GT_METRICS" ]]; then
  echo "[phase5-qc-roundtrip] missing ground-truth metrics: $GT_METRICS" >&2
  exit 3
fi
if [[ ! -f "$NIGHTLY_SIGNOFF_JSON" ]]; then
  echo "[phase5-qc-roundtrip] missing nightly signoff json: $NIGHTLY_SIGNOFF_JSON" >&2
  exit 3
fi

echo "[phase5-qc-roundtrip] out_dir=$OUT_DIR"
echo "[phase5-qc-roundtrip] stamp=$STAMP"
echo "[phase5-qc-roundtrip] gt_metrics=$GT_METRICS"
echo "[phase5-qc-roundtrip] nightly_signoff=$NIGHTLY_SIGNOFF_JSON"

python scripts/run_phase4_fresh_recapture_signoff.py \
  --hard-iters 1 \
  --nightly-iters 0 \
  --duration-seconds 28 \
  --out-json "$HARD_SIGNOFF_JSON" \
  --out-md "$HARD_SIGNOFF_MD"

python scripts/curate_phase4_fullpane_visual_pack.py \
  --out-dir "$FLAT_BUNDLE_DIR" \
  --scenarios-root docs_tmp/tmux_captures/scenarios/phase4_replay

python scripts/validate_phase4_visual_pack.py \
  --pack-dir "$FLAT_BUNDLE_DIR" \
  --output-json "$FLAT_BUNDLE_VALIDATION_JSON"

python scripts/generate_phase5_footer_qc_pack.py \
  --hard-signoff-json "$HARD_SIGNOFF_JSON" \
  --nightly-signoff-json "$NIGHTLY_SIGNOFF_JSON" \
  --flat-bundle-dir "$FLAT_BUNDLE_DIR" \
  --out-dir "$FOOTER_QC_DIR"

python scripts/check_phase5_footer_contrast_gate.py \
  --footer-qc-pack-json "$FOOTER_QC_DIR/footer_qc_pack.json" \
  --output-json "$FOOTER_CONTRAST_JSON" \
  --output-md "$FOOTER_CONTRAST_MD"

python scripts/build_phase5_visual_drift_metrics.py \
  --flat-bundle-dir "$FLAT_BUNDLE_DIR" \
  --out-json "$VISUAL_METRICS_JSON"

python scripts/validate_phase5_replay_reliability.py \
  --run-root docs_tmp/tmux_captures/scenarios/phase4_replay \
  --scenario-set all \
  --contract-file config/text_contracts/phase4_text_contract_v1.json \
  --expected-render-profile phase4_locked_v5 \
  --fail-on-missing-scenarios \
  --output-json "$RELIABILITY_JSON" \
  --output-md "$RELIABILITY_MD"

python scripts/build_phase5_reliability_flake_report.py \
  --reliability-glob "$OUT_DIR/phase5_roundtrip_reliability_*.json" \
  --check-name isolated_low_edge_rows_total \
  --output-json "$RELIABILITY_FLAKE_JSON" \
  --output-md "$RELIABILITY_FLAKE_MD"

python scripts/check_phase5_reliability_flake_budget.py \
  --reliability-flake-json "$RELIABILITY_FLAKE_JSON" \
  --max-persistent 0 \
  --max-active 0 \
  --max-intermittent 0 \
  --max-transient 1 \
  --output-json "$RELIABILITY_FLAKE_GATE_JSON" \
  --output-md "$RELIABILITY_FLAKE_GATE_MD"

python scripts/validate_phase5_artifact_integrity.py \
  --run-root docs_tmp/tmux_captures/scenarios/phase4_replay \
  --scenario-set all \
  --contract-file config/text_contracts/phase4_text_contract_v1.json \
  --expected-render-profile phase4_locked_v5 \
  --default-max-isolated-edge-rows-total 1 \
  --max-line-gap-nonzero-frames 0 \
  --fail-on-missing-scenarios \
  --output-json "$ARTIFACT_JSON" \
  --output-md "$ARTIFACT_MD"

python scripts/validate_phase5_advanced_behavior_stress.py \
  --run-root docs_tmp/tmux_captures/scenarios/phase4_replay \
  --max-frame-gap-seconds 3.2 \
  --max-unchanged-streak-seconds 2.2 \
  --min-active-coverage 0.01 \
  --min-nonzero-deltas 1 \
  --fail-on-missing-scenarios \
  --output-json "$ADVANCED_JSON" \
  --output-md "$ADVANCED_MD"

BASELINE_LATENCY_JSON=""
for cand in $(ls -1t "$OUT_DIR"/phase5_roundtrip_latency_trend_*.json 2>/dev/null || true); do
  if [[ "$cand" == "$LATENCY_JSON" ]]; then
    continue
  fi
  BASELINE_LATENCY_JSON="$cand"
  break
done

LATENCY_BASELINE_ARGS=()
if [[ -n "$BASELINE_LATENCY_JSON" ]]; then
  LATENCY_BASELINE_ARGS+=(--baseline-json "$BASELINE_LATENCY_JSON")
fi

python scripts/build_phase5_latency_trend_report.py \
  --run-root docs_tmp/tmux_captures/scenarios/phase4_replay \
  --scenario-set all \
  --contract-file config/text_contracts/phase4_text_contract_v1.json \
  --max-first-frame-seconds 3.0 \
  --max-replay-first-frame-seconds 2.0 \
  --max-duration-seconds 25.0 \
  --max-stall-seconds 3.0 \
  --max-frame-gap-seconds 3.2 \
  "${LATENCY_BASELINE_ARGS[@]}" \
  --output-json "$LATENCY_JSON" \
  --output-md "$LATENCY_MD"

DRIFT_BOOTSTRAP_ARGS=()
if [[ -z "$BASELINE_LATENCY_JSON" ]]; then
  DRIFT_BOOTSTRAP_ARGS+=(--allow-bootstrap-no-baseline)
fi

ALLOWLIST_ARGS=()
if [[ -f "$ALLOWLIST_JSON" ]]; then
  ALLOWLIST_ARGS+=(--allowlist-json "$ALLOWLIST_JSON")
fi

python scripts/check_phase5_latency_drift_gate.py \
  --latency-trend-json "$LATENCY_JSON" \
  --max-threshold-ratio 0.95 \
  --max-mean-delta-first-frame 0.20 \
  --max-mean-delta-replay-first-frame 0.20 \
  --max-mean-delta-duration 0.60 \
  --max-mean-delta-stall 0.20 \
  --max-mean-delta-max-gap 0.20 \
  "${DRIFT_BOOTSTRAP_ARGS[@]}" \
  --output-json "$LATENCY_DRIFT_GATE_JSON" \
  --output-md "$LATENCY_DRIFT_GATE_MD"

TOP_CANARY_SCENARIO="$(python - <<'PY' "$LATENCY_JSON"
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
try:
    obj = json.loads(p.read_text(encoding='utf-8'))
except Exception:
    print("")
    raise SystemExit(0)
near = obj.get("near_threshold", {})
top = near.get("top", []) if isinstance(near, dict) else []
if isinstance(top, list) and top and isinstance(top[0], dict):
    print(str(top[0].get("scenario") or ""))
else:
    print("")
PY
)"

if [[ -n "$TOP_CANARY_SCENARIO" ]]; then
  HOTSPOT_WAIT_ARGS=()
  if [[ -f "$EXPECTED_WAITS_JSON" ]]; then
    HOTSPOT_WAIT_ARGS=(--expected-waits-json "$EXPECTED_WAITS_JSON")
  fi

  python scripts/analyze_phase5_frame_gap_hotspots.py \
    --run-root docs_tmp/tmux_captures/scenarios/phase4_replay \
    --scenario "$TOP_CANARY_SCENARIO" \
    --top-k 12 \
    "${HOTSPOT_WAIT_ARGS[@]}" \
    --output-json "$CANARY_HOTSPOT_JSON" \
    --output-md "$CANARY_HOTSPOT_MD"

  python scripts/build_phase5_latency_canary_timeseries.py \
    --trend-glob "$OUT_DIR/phase5_roundtrip_latency_trend_*.json" \
    --scenario "$TOP_CANARY_SCENARIO" \
    --metric max_frame_gap_seconds \
    --output-json "$CANARY_TIMESERIES_JSON" \
    --output-md "$CANARY_TIMESERIES_MD"

  python scripts/check_phase5_canary_wait_alignment.py \
    --canary-hotspot-json "$CANARY_HOTSPOT_JSON" \
    --max-top-unexpected-waits 0 \
    --output-json "$CANARY_WAIT_GATE_JSON" \
    --output-md "$CANARY_WAIT_GATE_MD"
fi

HOTSPOT_RANK_ARGS=()
if [[ -n "$TOP_CANARY_SCENARIO" && -f "$CANARY_HOTSPOT_JSON" ]]; then
  HOTSPOT_RANK_ARGS=(--canary-hotspot "$CANARY_HOTSPOT_JSON")
fi

python scripts/phase5_rank_replay_discrepancies.py \
  --visual-metrics "$VISUAL_METRICS_JSON" \
  --ground-truth-metrics "$GT_METRICS" \
  --footer-qc-pack "$FOOTER_QC_DIR/footer_qc_pack.json" \
  --advanced-stress "$ADVANCED_JSON" \
  --reliability-flake "$RELIABILITY_FLAKE_JSON" \
  --artifact-integrity "$ARTIFACT_JSON" \
  --latency-trend "$LATENCY_JSON" \
  --latency-drift-gate "$LATENCY_DRIFT_GATE_JSON" \
  "${HOTSPOT_RANK_ARGS[@]}" \
  "${ALLOWLIST_ARGS[@]}" \
  --out-json "$RANKING_JSON" \
  --out-md "$RANKING_MD"

python scripts/check_phase5_discrepancy_budget_gate.py \
  --ranking-json "$RANKING_JSON" \
  --max-high 0 \
  --max-medium 0 \
  --max-low 0 \
  --max-unknown 0 \
  --max-non-info 0 \
  --max-top-risk 0.0 \
  --output-json "$RANKING_GATE_JSON" \
  --output-md "$RANKING_GATE_MD"

python scripts/build_phase5_postcert_monitoring_bundle.py \
  --stamp "$STAMP" \
  --footer-gate-json "$FOOTER_CONTRAST_JSON" \
  --discrepancy-budget-json "$RANKING_GATE_JSON" \
  --reliability-budget-json "$RELIABILITY_FLAKE_GATE_JSON" \
  --latency-drift-json "$LATENCY_DRIFT_GATE_JSON" \
  --wait-alignment-json "$CANARY_WAIT_GATE_JSON" \
  --ranking-json "$RANKING_JSON" \
  --latency-trend-json "$LATENCY_JSON" \
  --output-json "$POSTCERT_MONITOR_JSON" \
  --output-md "$POSTCERT_MONITOR_MD"

echo "[phase5-qc-roundtrip] pass"
echo "[phase5-qc-roundtrip] outputs:"
echo "  $HARD_SIGNOFF_JSON"
echo "  $FLAT_BUNDLE_DIR"
echo "  $FOOTER_QC_DIR/footer_qc_pack.json"
echo "  $FOOTER_CONTRAST_JSON"
echo "  $VISUAL_METRICS_JSON"
echo "  $RELIABILITY_JSON"
echo "  $RELIABILITY_FLAKE_JSON"
echo "  $RELIABILITY_FLAKE_GATE_JSON"
echo "  $ARTIFACT_JSON"
echo "  $ADVANCED_JSON"
echo "  $LATENCY_JSON"
echo "  $LATENCY_DRIFT_GATE_JSON"
echo "  $RANKING_JSON"
echo "  $RANKING_GATE_JSON"
echo "  $POSTCERT_MONITOR_JSON"
if [[ -n "$TOP_CANARY_SCENARIO" ]]; then
  echo "  $CANARY_HOTSPOT_JSON"
  echo "  $CANARY_TIMESERIES_JSON"
  echo "  $CANARY_WAIT_GATE_JSON"
fi
