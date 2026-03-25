# DARWIN Stage-5 Systems-Weighted Live Status

Date: 2026-03-25
Status: bounded live-review bundle landed
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

The fresh systems-weighted live rerun is currently stalling behind the live provider path. This status is therefore based on the current live policy-stability evidence plus the newly operational systems-weighted policy slice.

That is acceptable for this bounded review because the question here is whether the new lane weighting is supported by the current live evidence, not whether a brand new live campaign already changed the result.

## Result

- `systems_primary_supported=true`
- `repo_challenge_supported=true`

The current live evidence supports the systems-primary / Repo_SWE-challenge split.
