# DARWIN Stage-5 Cross-Lane Review Status

Date: 2026-03-25
Status: cross-lane review slice landed
References:
- `artifacts/darwin/stage5/cross_lane_review/cross_lane_review_v0.json`
- `artifacts/darwin/stage5/policy_stability/policy_stability_v0.json`
- `artifacts/darwin/stage5/repo_swe_family_ab/repo_swe_family_ab_v0.json`

## What landed

- a derived Stage-5 cross-lane review bundle
- an explicit lane-weight decision for the current compounding proof surface
- a bounded Repo_SWE family-integrity check inside the review layer
- a machine-readable next-step pointer for the next Stage-5 proving slice

## Current cross-lane read

- `lane.systems`
  - weight: `primary_proving_lane`
  - stability: `mixed_positive`
  - `7` reuse-lift vs `5` no-lift
- `lane.repo_swe`
  - weight: `challenge_lane`
  - stability: `mixed_negative`
  - `4` reuse-lift, `2` flat, `6` no-lift

## Repo_SWE family note

The current Repo_SWE family A/B surface is marked `stale_or_incomplete`.

That read is now source-backed, not only review-derived. The repaired family artifact itself carries the stale status because one counted round is still non-claim-eligible. The cross-lane review therefore does not treat the current Repo_SWE family artifact as proof of a settled family winner.

## Interpretation

The current Stage-5 compounding surface is no longer balanced across the two primary lanes:

- Systems is the cleaner current proving lane
- Repo_SWE remains interpretable enough to keep in scope
- Repo_SWE should not currently carry equal proving weight until its family/protocol surface is clean again

## What this authorizes next

- keep `lane.systems` as the current primary proving lane
- keep `lane.repo_swe` as the bounded challenge lane
- move to a systems-weighted Stage-5 compounding review next
- keep transfer and composition closed
