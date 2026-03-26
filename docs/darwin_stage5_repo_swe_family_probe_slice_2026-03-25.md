# DARWIN Stage-5 Repo_SWE Family Probe Slice

Date: 2026-03-25
Status: landed
Scope: bounded Repo_SWE family-level adjustment after the policy-stability deadband slice
References:
- `docs/darwin_stage5_execution_plan_2026-03-20.md`
- `docs/darwin_stage5_policy_stability_status_2026-03-22.md`
- `artifacts/darwin/stage4/family_program/family_registry_v0.json`

## Purpose

Repo_SWE no longer justified more raw repetition. The next bounded move was to change the family being probed under the same Stage-5 protocol.

## What this slice changes

- keeps Stage-5 bounded to `lane.repo_swe` and `lane.systems`
- leaves Systems unchanged
- adds a bounded Repo_SWE family probe that can replace the promoted topology family with the strongest withheld tool-scope family when:
  - Repo_SWE remains `mixed_negative` or `mixed_flat`
  - flat outcomes are materially present
- keeps the same comparison protocol:
  - `cold_start`
  - `warm_start`
  - `family_lockout`

## Authorized Repo_SWE family probe

- target family kind: `tool_scope`
- target operator: `mut.tool_scope.add_git_diff_v1`
- family source:
  - `component_family.stage4.tool_scope.policy.tool_scope.add_git_diff_v1.lane.repo_swe.v0`

## Boundary

- no transfer work
- no family composition
- no runtime-truth expansion
- no broader lane expansion
- no promotion-state rewrite for the withheld family

## Hard gate

This slice succeeds if it produces a cleaner directional compounding read for Repo_SWE than the prior flat-heavy topology probe, even if the result remains mixed.
