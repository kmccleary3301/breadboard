# DARWIN Stage-3 Systems Mutation Canary Status

Date: 2026-03-19
Status: bounded systems canary landed

## What landed

- `lane.systems` now has a bounded Stage-3 mutation canary
- the canary emits:
  - optimization target
  - candidate bundle
  - materialized candidate
  - evaluator pack with budget envelope

## Covered operators

- topology mutation
- policy-bundle mutation

## Read

This is enough to show the Stage-3 substrate path is not repo_swe-only, while still staying below broad real-inference scope.
