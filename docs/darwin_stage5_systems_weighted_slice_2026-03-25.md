# DARWIN Stage-5 Systems-Weighted Slice

Date: 2026-03-25
Status: defined and landed
References:
- `docs/darwin_stage5_cross_lane_review_2026-03-25.md`
- `artifacts/darwin/stage5/cross_lane_review/cross_lane_review_v0.json`

## Purpose

This slice makes the current Stage-5 proving-weight decision operational:

- `lane.systems` becomes the explicit primary proving lane
- `lane.repo_swe` remains in scope as a bounded challenge lane

The slice does not widen runtime truth, transfer, or family composition.

## Code ownership

- `breadboard_ext/darwin/stage5.py`
  - consume cross-lane review state
  - express lane-weighted policy behavior
- `scripts/run_darwin_stage5_systems_weighted_compounding_v0.py`
  - emit a bounded systems-weighted tranche bundle
- `tests/test_darwin_stage5.py`
  - lock lane-weighted policy behavior
- `tests/test_run_darwin_stage5_compounding_pilot_v0.py`
  - lock the systems-weighted bundle shape

## Required behavior

- Systems primary lane:
  - one mutation arm
  - `repetition_count >= 8`
  - explicit `cross_lane_weighting.systems_primary=true`
- Repo_SWE challenge lane:
  - one mutation arm
  - `repetition_count = 4`
  - no automatic family probe while the current family A/B surface is stale
  - explicit `cross_lane_weighting.repo_swe_challenge=true`

## Hard boundary

- no transfer-matrix widening
- no family-composition work
- no new provider-economics claim
- no reopening of Stage-5 family A/B as if it were already clean
