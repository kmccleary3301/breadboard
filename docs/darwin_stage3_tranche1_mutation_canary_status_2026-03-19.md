# DARWIN Stage-3 Tranche-1 Mutation Canary Status

Date: 2026-03-19
Status: narrow substrate-backed mutation canary landed
References:
- `docs/darwin_stage3_optimize_object_mapping_2026-03-19.md`
- `docs/darwin_stage3_tranche1_uptake_gate_2026-03-19.md`
- `docs/darwin_stage3_tranche1_budget_ledger_status_2026-03-19.md`

## What landed

- `lane.repo_swe` search-smoke mutations now emit substrate-backed Stage-3 artifacts:
  - optimization target
  - candidate bundle
  - materialized candidate
- the canary currently covers:
  - topology mutation
  - budget-class mutation
  - tool-scope mutation
  - policy-bundle mutation
- each canary mutation is validated through the shared optimize substrate with:
  - one selected mutable locus
  - bounded candidate validation
  - explicit blast-radius metadata

## Why this matters

This is the first Stage-3 step where DARWIN search-smoke work uses the shared optimization substrate as more than documentation.

The runtime boundary still holds:

- search execution still runs through the existing DARWIN lane runner
- `ExecutionPlan` consumption remains limited to the existing narrow binding subset
- no new runtime truth was introduced
- no real-inference spend was widened

## Boundary

This canary does not yet authorize:

- substrate-backed mutation on every search-enabled lane
- broad runtime mutation application
- systems-heavy proving
- async search
- larger real-inference campaigns
