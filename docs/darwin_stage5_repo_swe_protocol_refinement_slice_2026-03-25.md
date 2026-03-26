# DARWIN Stage-5 Repo_SWE Protocol Refinement Slice

Date: 2026-03-25
Status: landed
Scope: refine Repo_SWE compounding interpretation after the family A/B result
References:
- `docs/darwin_stage5_repo_swe_family_ab_gate_2026-03-25.md`
- `artifacts/darwin/stage5/repo_swe_family_ab/repo_swe_family_ab_v0.json`

## Purpose

The Repo_SWE family A/B surface exposed a protocol issue: tiny runtime wins could still beat materially worse cost outcomes. This slice replaces that behavior with a dominance-with-tolerance rule.

## What changed

- score remains dominant if it changes
- otherwise:
  - runtime wins count only when cost is not materially worse
  - cost wins count only when runtime is not materially worse
  - cross-metric tradeoffs collapse to `flat`

## Boundary

- no transfer work
- no family composition
- no runtime-truth expansion
- no lane expansion

## Success condition

This slice succeeds if the Repo_SWE family choice remains legible under a more defensible comparison rule.
