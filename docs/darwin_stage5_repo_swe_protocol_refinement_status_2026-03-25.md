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

The family choice did not flip under the stronger rule:

- `tool_scope`
  - `5` reuse-lift
  - `3` flat
  - `4` no-lift
- `topology`
  - `3` reuse-lift
  - `5` flat
  - `4` no-lift

## Interpretation

This is a useful result.

- the family choice is now cleaner
- `tool_scope` still beats `topology`
- the Stage-5 Repo_SWE problem is no longer "which family?" but "how to lift the selected family from mixed into positive stability?"
