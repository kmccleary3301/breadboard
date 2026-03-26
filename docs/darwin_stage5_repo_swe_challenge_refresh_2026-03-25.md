# DARWIN Stage-5 Repo_SWE Challenge Refresh Slice

Date: 2026-03-25
Status: landed
References:
- `artifacts/darwin/stage5/repo_swe_challenge_refresh/compounding_pilot_v0.json`
- `artifacts/darwin/stage5/repo_swe_family_ab/repo_swe_family_ab_v0.json`

## Purpose

Refresh Repo_SWE as a challenge lane after the family-surface repair.

This slice is not new family exploration. It is a freshness repair so Stage-5 policy and review layers consume current, non-stale Repo_SWE evidence.

## Allowed surface

- same lane: `lane.repo_swe`
- same budget class: `class_a`
- same comparison protocol:
  - `cold_start`
  - `warm_start`
  - `family_lockout`
- same family semantics as the repaired canonical Repo_SWE family A/B surface
