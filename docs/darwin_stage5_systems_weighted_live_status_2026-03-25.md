# DARWIN Stage-5 Systems-Weighted Live Status

Date: 2026-03-25
Status: refreshed after clean completed systems-weighted live run
References:
- `artifacts/darwin/stage5/systems_weighted_live_review/systems_weighted_live_review_v0.json`
- `artifacts/darwin/stage5/policy_stability/policy_stability_v0.json`
- `artifacts/darwin/stage5/cross_lane_review/cross_lane_review_v0.json`

## What landed

- a bounded Stage-5 live-review bundle over the current systems-weighted proving surface
- an explicit check that the new lane weighting is supported by the current live evidence
- an explicit next-step pointer that keeps transfer and composition closed

## Current live read

- `lane.systems`
  - weight: `primary_proving_lane`
  - `24` claim-eligible comparisons
  - `7` reuse-lift vs `5` no-lift
  - `mixed_positive`
- `lane.repo_swe`
  - weight: `challenge_lane`
  - `24` claim-eligible comparisons
  - `4` reuse-lift, `2` flat, `6` no-lift
  - `mixed_negative`

## Operational note

The systems-weighted live review now carries a clean completed live-run status:

- `systems_weighted_live_run_status=complete`
- `systems_weighted_bundle_complete=true`
- per-lane execution status: `completed_live`

This removes the main execution-hygiene blocker that was keeping the family-aware proving surface below gate quality.

## Result

- `systems_primary_supported=true`
- `repo_challenge_supported=true`

The current live evidence supports the systems-primary / Repo_SWE-challenge split.
