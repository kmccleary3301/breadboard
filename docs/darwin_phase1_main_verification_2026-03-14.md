# DARWIN Phase-1 Merged-State Verification

Date: 2026-03-14

Branch verified: `main`

## Validation commands

- `pytest -q tests/test_darwin_contract_pack_v0.py tests/test_bootstrap_darwin_campaign_specs_v0.py tests/test_build_darwin_topology_family_runner_v0.py tests/test_run_darwin_t1_baseline_scorecard_v1.py tests/test_build_darwin_weekly_packet_v1.py tests/test_run_darwin_t1_live_baselines_v1.py tests/test_emit_darwin_evidence_and_claims_v1.py tests/test_build_darwin_bootstrap_rollup_v0.py tests/test_check_darwin_lane_readiness_v0.py tests/test_run_darwin_t1_smoke_v1.py tests/test_build_darwin_typed_search_core_v1.py tests/test_build_darwin_future_lane_placeholders_v0.py tests/test_run_darwin_t2_search_smoke_v1.py tests/test_build_darwin_invalid_comparison_ledger_v1.py tests/test_emit_darwin_search_evidence_v1.py tests/test_run_darwin_phase1_orchestration_v1.py tests/test_run_darwin_scheduling_lane_baseline_v0.py tests/test_run_darwin_research_lane_baseline_v0.py tests/test_build_darwin_compute_normalized_view_v1.py tests/test_build_darwin_comparative_dossier_v1.py tests/test_run_darwin_phase1_replay_audit_v1.py tests/test_emit_darwin_phase1_final_evidence_v1.py`
- `python scripts/run_darwin_phase1_orchestration_v1.py --json`
- `python scripts/run_darwin_phase1_replay_audit_v1.py --json`
- `python scripts/emit_darwin_phase1_final_evidence_v1.py --json`

## Results

- `36` DARWIN tests passed on merged `main`.
- Orchestration emitted `6` bootstrap specs, `6` live lanes, and `4` search-enabled lanes.
- Replay audit reported `all_stable=true`.
- Final evidence emission completed with `3` final program claims.

This document records the required fresh merged-state verification step for DARWIN Phase-1 closure.
