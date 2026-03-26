# DARWIN Stage-5 Family-Aware Proving Status

Date: 2026-03-25
Status: family-aware proving completion slice landed
References:
- `artifacts/darwin/stage5/compounding_quality/compounding_quality_v0.json`
- `artifacts/darwin/stage5/cross_lane_review/cross_lane_review_v0.json`
- `artifacts/darwin/stage5/systems_weighted_live_review/systems_weighted_live_review_v0.json`
- `artifacts/darwin/stage5/repo_swe_family_ab/repo_swe_family_ab_v0.json`
- `artifacts/darwin/stage5/family_aware_scorecard/family_aware_scorecard_v0.json`

## What landed

- `SearchPolicyV2` now consumes Repo_SWE family-surface status explicitly
- stale Repo_SWE family surfaces now block automatic family probing
- Repo_SWE family A/B is now cleanly repaired to `settled_topology`
- a compact compounding-quality summary now exposes per-lane proving quality with no stale-family contamination
- the systems-weighted live review now carries a completed live-run status
- a family-aware scorecard now exposes the current proving surface directly

## Current quality read

- `lane.systems`
  - weight: `primary_proving_lane`
  - stability: `mixed_positive`
  - `7` reuse-lift vs `5` no-lift
- `lane.repo_swe`
  - weight: `challenge_lane`
  - stability: `mixed_negative`
  - family surface: `settled_topology`
  - stale-family contamination: `false`
- systems-weighted live run status: `complete`

## Interpretation

The Stage-5 proving surface is now both honest and current enough to pass a Tranche-2 gate.

Systems remains the cleaner lane. Repo_SWE remains in scope as the weaker challenge lane, but it is no longer stale. The next bounded move is no longer proving repair. It is the Tranche-2 review/gate and then scaled compounding planning.
