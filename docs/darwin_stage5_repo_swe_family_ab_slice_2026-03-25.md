# DARWIN Stage-5 Repo_SWE Family A/B Slice

Date: 2026-03-25
Status: landed
Scope: direct Repo_SWE family comparison under one shared Stage-5 protocol surface
References:
- `docs/darwin_stage5_repo_swe_family_probe_gate_2026-03-25.md`
- `artifacts/darwin/stage5/repo_swe_family_ab/repo_swe_family_ab_v0.json`

## Purpose

The prior Stage-5 Repo_SWE decision still relied on separate slices. This slice creates a single bounded A/B surface for the two relevant Repo_SWE families:

- promoted topology family
- withheld tool-scope family

## Boundary

- Repo_SWE only
- same Stage-5 live compounding protocol for both families
- no transfer
- no family composition
- no promotion-state rewrite

## Success condition

This slice succeeds if it makes the next Repo_SWE family choice legible under one shared live surface, even if neither family is yet strongly positive.
