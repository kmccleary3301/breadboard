# DARWIN Stage-5 Repo_SWE Family A/B Status

Date: 2026-03-25
Status: landed
References:
- `docs/darwin_stage5_repo_swe_family_ab_slice_2026-03-25.md`
- `artifacts/darwin/stage5/repo_swe_family_ab/repo_swe_family_ab_v0.json`

## What landed

- explicit family override support for Repo_SWE Stage-5 runs
- a bounded live A/B runner comparing:
  - `topology`
  - `tool_scope`
- one shared family-comparison artifact

## Current result

- `tool_scope`
  - `5` reuse-lift
  - `3` flat
  - `4` no-lift
- `topology`
  - `3` reuse-lift
  - `5` flat
  - `4` no-lift

## Interpretation

The tool-scope family is the better current Repo_SWE family under the Stage-5 protocol.

- it produces more reuse-lift
- it produces fewer flat outcomes
- it does not eliminate Repo_SWE instability

This does not yet prove stable scalable compounding. It does give a cleaner family choice for the next Repo_SWE-bounded slice.
