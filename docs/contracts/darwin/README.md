# DARWIN Contract Pack (V0)

This directory defines the Phase-1 DARWIN control-plane and evaluation-plane contracts that sit above BreadBoard’s general kernel and above the older EvoLake bridge scaffolding.

## Scope

Phase-1 T0 freezes:

- DARWIN naming and legacy `evolake` compatibility posture
- typed campaign / policy / candidate / evaluation / evidence / claim contracts
- lane registry v0
- policy registry v0
- weekly evidence packet template
- claim ladder and evidence-gate policy

## Files

### Human-readable docs

- `DARWIN_CONTRACT_PACK_V0.md`
- `DARWIN_LANE_REGISTRY_V0.md`
- `DARWIN_POLICY_REGISTRY_V0.md`
- `DARWIN_CLAIM_LADDER_V0.md`
- `DARWIN_WEEKLY_EVIDENCE_PACKET_V0.md`
- `DARWIN_COMPATIBILITY_MAP_V0.md`
- `DARWIN_ARCHITECTURE_BOUNDARY_V0.md`
- `DARWIN_ARTIFACT_LAYOUT_V0.md`
- `DARWIN_REPO_SWE_BASELINE_V0.md`
- `DARWIN_SCHEDULING_BASELINE_V0.md`
- `DARWIN_RESEARCH_BASELINE_V0.md`
- `DARWIN_TOPOLOGY_COMPATIBILITY_V1.md`
- `DARWIN_TYPED_SEARCH_CORE_V1.md`
- `DARWIN_TRANSFER_PROTOCOL_V1.md`
- `DARWIN_COMPUTE_NORMALIZED_SCORECARD_V1.md`
- `DARWIN_COMPUTE_NORMALIZED_SCORECARD_V2.md`
- `DARWIN_COST_ACCOUNTING_CLASSIFICATION_V0.md`
- `DARWIN_MODEL_ROUTING_POLICY_V0.md`
- `DARWIN_TRANSFER_FAMILY_POLICY_V0.md`
- `DARWIN_LINEAGE_POLICY_V0.md`
- `DARWIN_EXTERNAL_SAFE_EVIDENCE_POLICY_V0.md`
- `DARWIN_STAGE3_COMPONENT_FAMILY_POLICY_V0.md`
- `DARWIN_STAGE3_COMPONENT_PROMOTION_POLICY_V0.md`
- `DARWIN_STAGE3_BOUNDED_TRANSFER_POLICY_V0.md`
- `DARWIN_STAGE3_COMPONENT_LIFECYCLE_V0.md`
- `DARWIN_STAGE4_COMPONENT_FAMILY_POLICY_V0.md`
- `DARWIN_STAGE4_COMPONENT_PROMOTION_POLICY_V0.md`
- `DARWIN_STAGE4_BOUNDED_TRANSFER_POLICY_V0.md`
- `DARWIN_STAGE5_BOUNDED_TRANSFER_POLICY_V0.md`
- `DARWIN_STAGE5_COMPOSITION_CANARY_POLICY_V0.md`

### Machine-readable schemas

- `schemas/campaign_spec_v0.schema.json`
- `schemas/policy_bundle_v0.schema.json`
- `schemas/candidate_artifact_v0.schema.json`
- `schemas/evaluation_record_v0.schema.json`
- `schemas/effective_config_v0.schema.json`
- `schemas/execution_plan_v0.schema.json`
- `schemas/effective_policy_v0.schema.json`
- `schemas/evaluator_pack_v0.schema.json`
- `schemas/component_ref_v0.schema.json`
- `schemas/decision_record_v0.schema.json`
- `schemas/evolution_ledger_v0.schema.json`
- `schemas/evidence_bundle_v0.schema.json`
- `schemas/claim_record_v0.schema.json`
- `schemas/lane_registry_v0.schema.json`
- `schemas/policy_registry_v0.schema.json`
- `schemas/weekly_evidence_packet_v0.schema.json`

### Canonical registries

- `registries/lane_registry_v0.json`
- `registries/policy_registry_v0.json`

### Bootstrap / T1 emitters

- `scripts/bootstrap_darwin_campaign_specs_v0.py`
- `scripts/build_darwin_topology_family_runner_v0.py`
- `scripts/run_darwin_t1_baseline_scorecard_v1.py`
- `scripts/build_darwin_weekly_packet_v1.py`
- `scripts/run_darwin_t1_live_baselines_v1.py`
- `scripts/emit_darwin_evidence_and_claims_v1.py`
- `scripts/build_darwin_bootstrap_rollup_v0.py`
- `scripts/check_darwin_lane_readiness_v0.py`
- `scripts/run_darwin_t1_smoke_v1.py`
- `scripts/build_darwin_future_lane_placeholders_v0.py`
- `scripts/build_darwin_typed_search_core_v1.py`
- `scripts/run_darwin_t2_search_smoke_v1.py`
- `scripts/build_darwin_invalid_comparison_ledger_v1.py`
- `scripts/build_darwin_evolution_ledger_v0.py`
- `scripts/build_darwin_compute_normalized_view_v1.py`
- `scripts/build_darwin_compute_normalized_view_v2.py`
- `scripts/build_darwin_compute_normalized_review_v0.py`
- `scripts/build_darwin_compute_normalized_memo_v0.py`
- `scripts/build_darwin_transfer_family_view_v0.py`
- `scripts/build_darwin_lineage_review_v0.py`
- `scripts/build_darwin_transfer_lineage_proving_review_v0.py`
- `scripts/build_darwin_external_safe_claim_subset_v0.py`
- `scripts/build_darwin_external_safe_invalidity_summary_v0.py`
- `scripts/build_darwin_external_safe_reviewer_summary_v0.py`
- `scripts/build_darwin_external_safe_memo_v0.py`
- `scripts/build_darwin_external_safe_packet_v0.py`
- `scripts/build_darwin_transfer_family_view_v0.py`
- `scripts/build_darwin_lineage_review_v0.py`
- `scripts/build_darwin_comparative_dossier_v1.py`
- `scripts/emit_darwin_search_evidence_v1.py`
- `scripts/run_darwin_phase1_replay_audit_v1.py`
- `scripts/emit_darwin_phase1_final_evidence_v1.py`
- `scripts/run_darwin_phase1_orchestration_v1.py`
- `scripts/run_darwin_research_lane_baseline_v0.py`
- `scripts/run_darwin_stage3_bounded_inference_campaign_v0.py`
- `scripts/build_darwin_stage3_operator_ev_report_v0.py`
- `scripts/build_darwin_stage3_topology_ev_report_v0.py`
- `scripts/build_darwin_stage3_invalidity_summary_v0.py`
- `scripts/build_darwin_stage3_verification_bundle_v0.py`
- `scripts/build_darwin_stage3_component_candidates_v0.py`
- `scripts/run_darwin_stage3_component_replay_v0.py`
- `scripts/build_darwin_stage3_component_promotion_report_v0.py`
- `scripts/build_darwin_stage3_bounded_transfer_outcomes_v0.py`
- `scripts/build_darwin_stage3_failed_transfer_taxonomy_v0.py`
- `scripts/build_darwin_stage3_component_transfer_verification_bundle_v0.py`
- `scripts/build_darwin_stage3_component_registry_v0.py`
- `scripts/build_darwin_stage3_promoted_family_artifact_v0.py`
- `scripts/build_darwin_stage3_second_family_decision_v0.py`
- `scripts/build_darwin_stage3_component_scorecard_v0.py`
- `scripts/build_darwin_stage3_component_memo_v0.py`
- `scripts/build_darwin_stage3_consolidated_verification_bundle_v0.py`
- `scripts/run_darwin_stage4_live_economics_pilot_v0.py`
- `scripts/run_darwin_stage4_systems_live_pilot_v0.py`
- `scripts/build_darwin_stage4_live_readiness_v0.py`
- `scripts/build_darwin_stage4_matched_budget_view_v1.py`
- `scripts/build_darwin_stage4_operator_ev_report_v0.py`
- `scripts/build_darwin_stage4_topology_ev_report_v0.py`
- `scripts/build_darwin_stage4_systems_ev_bundle_v0.py`
- `scripts/build_darwin_stage4_family_candidates_v0.py`
- `scripts/build_darwin_stage4_family_promotion_report_v0.py`
- `scripts/build_darwin_stage4_second_family_decision_v0.py`
- `scripts/build_darwin_stage4_family_registry_v0.py`
- `scripts/build_darwin_stage4_bounded_transfer_outcomes_v0.py`
- `scripts/build_darwin_stage4_failed_transfer_taxonomy_v0.py`
- `scripts/build_darwin_stage4_family_scorecard_v0.py`
- `scripts/build_darwin_stage4_family_memo_v0.py`
- `scripts/build_darwin_stage4_family_verification_bundle_v0.py`
- `scripts/run_darwin_stage4_family_replay_audit_v0.py`
- `scripts/build_darwin_stage4_canonical_artifact_index_v0.py`
- `scripts/build_darwin_stage4_comparative_bundle_v0.py`
- `scripts/run_darwin_stage5_compounding_pilot_v0.py`
- `scripts/run_darwin_stage5_systems_compounding_pilot_v0.py`
- `scripts/run_darwin_stage5_multilane_compounding_v0.py`
- `scripts/run_darwin_stage5_systems_weighted_compounding_v0.py`
- `scripts/run_darwin_stage5_repo_swe_family_ab_v0.py`
- `scripts/run_darwin_stage5_scaled_compounding_v0.py`
- `scripts/build_darwin_stage5_policy_stability_v0.py`
- `scripts/build_darwin_stage5_repo_swe_family_ab_repair_v0.py`
- `scripts/build_darwin_stage5_cross_lane_review_v0.py`
- `scripts/build_darwin_stage5_systems_weighted_live_review_v0.py`
- `scripts/build_darwin_stage5_compounding_quality_v0.py`
- `scripts/build_darwin_stage5_family_aware_scorecard_v0.py`
- `scripts/build_darwin_stage5_compounding_rate_v0.py`
- `scripts/build_darwin_stage5_family_reuse_tracking_v0.py`
- `scripts/build_darwin_stage5_family_registry_v0.py`
- `scripts/build_darwin_stage5_third_family_decision_v0.py`
- `scripts/build_darwin_stage5_bounded_transfer_outcomes_v0.py`
- `scripts/build_darwin_stage5_failed_transfer_taxonomy_v0.py`
- `scripts/build_darwin_stage5_replay_posture_v0.py`
- `scripts/build_darwin_stage5_scaled_scorecard_v0.py`
- `scripts/build_darwin_stage5_scaled_memo_v0.py`
- `scripts/build_darwin_stage5_composition_canary_v0.py`
- `scripts/build_darwin_stage5_verification_bundle_v0.py`
- `scripts/build_darwin_stage5_canonical_artifact_index_v0.py`
- `scripts/build_darwin_stage5_comparative_bundle_v0.py`

## Current maintained entrypoints

For the current merged DARWIN surface, the maintained proving and review flow is:

1. run a bounded Stage-5 proving slice:
   - `scripts/run_darwin_stage5_compounding_pilot_v0.py`
   - `scripts/run_darwin_stage5_systems_weighted_compounding_v0.py`
   - `scripts/run_darwin_stage5_scaled_compounding_v0.py`
2. build the current Stage-5 review surfaces:
   - `scripts/build_darwin_stage5_compounding_quality_v0.py`
   - `scripts/build_darwin_stage5_family_aware_scorecard_v0.py`
   - `scripts/build_darwin_stage5_scaled_scorecard_v0.py`
   - `scripts/build_darwin_stage5_comparative_bundle_v0.py`
3. keep the contract layer authoritative:
   - `docs/contracts/darwin/DARWIN_CONTRACT_PACK_V0.md`
   - `docs/contracts/darwin/DARWIN_STAGE5_BOUNDED_TRANSFER_POLICY_V0.md`
   - `docs/contracts/darwin/DARWIN_STAGE5_COMPOSITION_CANARY_POLICY_V0.md`
4. use the current maintainer-only Stage-6 planning entrypoint under:
   - `docs/internals/DARWIN_STAGE6_DOCTRINE_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_EXECUTION_PLAN_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_ADR_SET_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_TRANCHE1_STATUS_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_TRANCHE1_DIAGNOSIS_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_TRANCHE1_REVIEW_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_TRANCHE1_GATE_2026-04-09.md`
5. use the current bounded Stage-6 operational canary and broader-transfer slice under:
   - `breadboard_ext/darwin/stage6.py`
   - `scripts/run_darwin_stage6_broader_transfer_canary_v0.py`
   - `scripts/run_darwin_stage6_broader_transfer_matrix_v0.py`
   - `docs/internals/DARWIN_STAGE6_TRANCHE2_STATUS_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_TRANCHE2_REVIEW_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_TRANCHE2_GATE_2026-04-09.md`
6. use the current bounded Stage-6 broader-compounding slice under:
   - `scripts/run_darwin_stage6_broader_compounding_v0.py`
   - `scripts/build_darwin_stage6_compounding_rate_v0.py`
   - `scripts/build_darwin_stage6_family_registry_v0.py`
   - `scripts/build_darwin_stage6_economics_attribution_v0.py`
   - `scripts/build_darwin_stage6_transfer_compounding_linkage_v0.py`
   - `scripts/build_darwin_stage6_replay_posture_v0.py`
   - `scripts/build_darwin_stage6_scorecard_v0.py`
   - `scripts/build_darwin_stage6_verification_bundle_v0.py`
   - `docs/internals/DARWIN_STAGE6_TRANCHE3_BASELINE_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_TRANCHE3_STATUS_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_TRANCHE3_REVIEW_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_TRANCHE3_GATE_2026-04-09.md`
7. use the current bounded Stage-6 composition-decision and pre-closeout surfaces under:
   - `scripts/build_darwin_stage6_composition_canary_v0.py`
   - `scripts/build_darwin_stage6_tranche4_family_registry_v0.py`
   - `scripts/build_darwin_stage6_tranche4_scorecard_v0.py`
   - `scripts/build_darwin_stage6_canonical_artifact_index_v0.py`
   - `scripts/build_darwin_stage6_comparative_bundle_v0.py`
   - `docs/internals/DARWIN_STAGE6_TRANCHE4_SLICE_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_TRANCHE4_STATUS_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_TRANCHE4_REVIEW_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_TRANCHE4_GATE_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_LATE_COMPARATIVE_EXECUTION_PLAN_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_CLAIM_BOUNDARY_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_LATE_COMPARATIVE_MEMO_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_PRECLOSEOUT_REVIEW_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_PRECLOSEOUT_GATE_2026-04-09.md`
8. use the final Stage-6 closeout surfaces under:
   - `docs/internals/DARWIN_STAGE6_CLOSEOUT_EXECUTION_PLAN_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_SIGNOFF_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_COMPLETION_GATE_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_CANONICAL_ARTIFACTS_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_FUTURE_ROADMAP_HANDOFF_2026-04-09.md`
   - `docs/internals/DARWIN_STAGE6_MAIN_VERIFICATION_2026-04-09.md`

## Legacy compatibility

DARWIN is the canonical program name. Existing stable `evolake` runtime identifiers, artifact keys, and extension names remain valid until a dedicated migration tranche lands. This pack therefore introduces DARWIN contracts without breaking the existing EvoLake bridge layer.
