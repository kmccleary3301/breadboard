# DARWIN Stage-5 Family Surface Repair Status

Date: 2026-03-25
Status: Repo_SWE family-surface repair landed
References:
- `artifacts/darwin/stage5/repo_swe_family_ab/repo_swe_family_ab_v0.json`
- `artifacts/darwin/stage5/cross_lane_review/cross_lane_review_v0.json`

## What landed

- atomic Repo_SWE family A/B writes now preserve the previous bundle on interrupted reruns
- a dedicated repair builder now rebuilds the canonical Repo_SWE family surface from per-round summaries only
- stale or incomplete round rows are now marked at the source artifact, not inferred only downstream
- the cross-lane review now consumes the source family-surface status directly

## Current repaired read

- Repo_SWE family surface: `stale_or_incomplete`
- valid round rows: `3`
- stale round rows: `1`
- preferred family: none

## Interpretation

The repair slice fixed the integrity problem at the artifact layer.

It did not magically settle Repo_SWE. The current family surface remains stale because one counted round is still non-claim-eligible. That is the correct current read, and the policy layer now treats it as a real blocker instead of a soft warning.
