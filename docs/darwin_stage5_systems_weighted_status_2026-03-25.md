# DARWIN Stage-5 Systems-Weighted Status

Date: 2026-03-25
Status: systems-weighted policy slice landed
References:
- `docs/darwin_stage5_systems_weighted_slice_2026-03-25.md`
- `artifacts/darwin/stage5/systems_weighted/systems_weighted_compounding_v0.json`
- `artifacts/darwin/stage5/cross_lane_review/cross_lane_review_v0.json`

## What landed

- Stage-5 search policy now consumes the cross-lane review surface
- Systems is operationalized as the current `primary_proving_lane`
- Repo_SWE is operationalized as the current `challenge_lane`
- a bounded systems-weighted compounding runner now emits a separate tranche bundle

## Current systems-weighted policy read

- `lane.systems`
  - lane weight: `primary_proving_lane`
  - mutation arms: `1`
  - repetition count: `8`
- `lane.repo_swe`
  - lane weight: `challenge_lane`
  - mutation arms: `1`
  - repetition count: `4`
  - automatic family probe: disabled

## Validation note

The emitted systems-weighted bundle is currently a scaffold validation surface:

- `lane.systems`: `32` valid comparisons across two rounds
- `lane.repo_swe`: `16` valid comparisons across two rounds
- claim-eligible live counts are `0` because this bounded validation run intentionally executed without provider env

That is sufficient for this slice because the objective was to make the cross-lane weighting operational, not to assert a new live compounding result.

## Interpretation

The current proving program is now mechanically honest:

- Systems carries the denser proving load
- Repo_SWE stays in scope without being given equal proving weight
- Repo_SWE family instability no longer leaks into the active policy path
