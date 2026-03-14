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

### Machine-readable schemas

- `schemas/campaign_spec_v0.schema.json`
- `schemas/policy_bundle_v0.schema.json`
- `schemas/candidate_artifact_v0.schema.json`
- `schemas/evaluation_record_v0.schema.json`
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
- `scripts/build_darwin_compute_normalized_view_v1.py`
- `scripts/build_darwin_comparative_dossier_v1.py`
- `scripts/emit_darwin_search_evidence_v1.py`
- `scripts/run_darwin_phase1_replay_audit_v1.py`
- `scripts/emit_darwin_phase1_final_evidence_v1.py`
- `scripts/run_darwin_phase1_orchestration_v1.py`
- `scripts/run_darwin_research_lane_baseline_v0.py`

## Legacy compatibility

DARWIN is the canonical program name. Existing stable `evolake` runtime identifiers, artifact keys, and extension names remain valid until a dedicated migration tranche lands. This pack therefore introduces DARWIN contracts without breaking the existing EvoLake bridge layer.
