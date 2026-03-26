# DARWIN Stage-5 Main Verification

Date: 2026-03-25
Status: complete on `main`

## Merged-state verification

- targeted merged-state suite passed: `60 passed`
- canonical Stage-5 builders rebuilt successfully on `main`
- contract-pack validation passed

## Verification scope

- `tests/test_darwin_stage4.py`
- `tests/test_darwin_stage5.py`
- `tests/test_build_darwin_stage5_policy_stability_v0.py`
- `tests/test_build_darwin_stage5_cross_lane_review_v0.py`
- `tests/test_build_darwin_stage5_systems_weighted_live_review_v0.py`
- `tests/test_build_darwin_stage5_compounding_quality_v0.py`
- `tests/test_build_darwin_stage5_family_aware_scorecard_v0.py`
- `tests/test_build_darwin_stage5_compounding_rate_v0.py`
- `tests/test_build_darwin_stage5_family_registry_v0.py`
- `tests/test_build_darwin_stage5_bounded_transfer_outcomes_v0.py`
- `tests/test_build_darwin_stage5_composition_canary_v0.py`
- `tests/test_build_darwin_stage5_canonical_artifact_index_v0.py`
- `tests/test_build_darwin_stage5_comparative_bundle_v0.py`
- `tests/test_build_darwin_stage5_scaled_scorecard_v0.py`
- selected Stage-5 runner tests for systems-weighted lane weighting and relative out-dir normalization

## Artifact rebuild scope

- `scripts/build_darwin_stage5_compounding_rate_v0.py`
- `scripts/build_darwin_stage5_family_reuse_tracking_v0.py`
- `scripts/build_darwin_stage5_third_family_decision_v0.py`
- `scripts/build_darwin_stage5_family_registry_v0.py`
- `scripts/build_darwin_stage5_bounded_transfer_outcomes_v0.py`
- `scripts/build_darwin_stage5_failed_transfer_taxonomy_v0.py`
- `scripts/build_darwin_stage5_composition_canary_v0.py`
- `scripts/build_darwin_stage5_replay_posture_v0.py`
- `scripts/build_darwin_stage5_compounding_quality_v0.py`
- `scripts/build_darwin_stage5_family_aware_scorecard_v0.py`
- `scripts/build_darwin_stage5_scaled_scorecard_v0.py`
- `scripts/build_darwin_stage5_scaled_memo_v0.py`
- `scripts/build_darwin_stage5_verification_bundle_v0.py`
- `scripts/build_darwin_stage5_canonical_artifact_index_v0.py`
- `scripts/build_darwin_stage5_comparative_bundle_v0.py`
