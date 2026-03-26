# DARWIN Stage-5 Repo_SWE Protocol Refinement Status

Date: 2026-03-25
Status: landed
References:
- `docs/darwin_stage5_repo_swe_protocol_refinement_slice_2026-03-25.md`
- `artifacts/darwin/stage5/repo_swe_family_ab/repo_swe_family_ab_v0.json`

## What landed

- dominance-with-tolerance compounding classification for Stage 5
- the refined rule was rerun against the live Repo_SWE family A/B surface

## Current result

The family choice did flip under the stronger rule and live rerun:

- `topology`
  - `3` reuse-lift
  - `5` flat
  - `4` no-lift
- `tool_scope`
  - `3` reuse-lift
  - `2` flat
  - `7` no-lift

## Interpretation

This is a useful result, but not the one from the prior slice.

- under the stronger rule, `tool_scope` no longer holds the better Repo_SWE profile
- `topology` is now the safer current family because it produces fewer outright no-lift outcomes
- Repo_SWE family choice is therefore not stable across protocol changes

The Stage-5 problem is no longer just single-lane family selection. It is now whether Repo_SWE should be tuned further at all before a broader cross-lane review.
