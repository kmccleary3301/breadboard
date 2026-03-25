# DARWIN Stage-5 Family-Aware Proving Status

Date: 2026-03-25
Status: family-aware proving integrity slice landed
References:
- `artifacts/darwin/stage5/compounding_quality/compounding_quality_v0.json`
- `artifacts/darwin/stage5/cross_lane_review/cross_lane_review_v0.json`
- `artifacts/darwin/stage5/systems_weighted_live_review/systems_weighted_live_review_v0.json`
- `artifacts/darwin/stage5/repo_swe_family_ab/repo_swe_family_ab_v0.json`

## What landed

- `SearchPolicyV2` now consumes Repo_SWE family-surface status explicitly
- stale Repo_SWE family surfaces now block automatic family probing
- a compact compounding-quality summary now exposes per-lane proving quality plus Repo_SWE contamination state
- the systems-weighted live review now distinguishes complete vs partial/stale live bundles

## Current quality read

- `lane.systems`
  - weight: `primary_proving_lane`
  - stability: `mixed_positive`
  - `7` reuse-lift vs `5` no-lift
- `lane.repo_swe`
  - weight: `challenge_lane`
  - stability: `mixed_negative`
  - family surface: `stale_or_incomplete`
  - stale-family contamination: `true`
- systems-weighted live run status: `partial_or_stale`

## Interpretation

The Stage-5 proving surface is now more honest than it was before this slice.

Systems remains the cleaner lane. Repo_SWE remains in scope, but only as a challenge lane until its family surface is clean again. The next bounded move is not transfer or composition. It is live execution hygiene plus one clean Repo_SWE challenge rerun.
