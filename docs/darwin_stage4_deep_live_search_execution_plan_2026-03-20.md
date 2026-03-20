# DARWIN Stage-4 Deep Live Search Execution Plan

Date: 2026-03-20
Status: executed bounded tranche
Scope: repeated live-provider campaigns on `lane.repo_swe` and `lane.systems`
References:
- `docs/darwin_stage4_execution_plan_2026-03-19.md`
- `docs/darwin_stage4_deep_live_search_gate_2026-03-20.md`
- `artifacts/darwin/stage4/deep_live_search/deep_live_search_v0.json`

## Objective

Turn the first one-round Stage-4 live pilots into repeated bounded live campaigns on the two primary lanes.

## Authorized lanes

- primary: `lane.repo_swe`, `lane.systems`
- watchdog/control only: `lane.harness`
- still out of proving center: `lane.scheduling`, `lane.atp`, `lane.research`

## Campaign classes

- `C0 Scout`
- `C1 Discovery`

No broader class expansion is authorized in this tranche.

## Repo_SWE search focus

- `mut.topology.single_to_pev_v1`
- `mut.tool_scope.add_git_diff_v1`
- `mut.policy.shadow_memory_enable_v1`
- `mut.budget.class_a_to_class_b_v1` as intentionally unmatched budget-pressure probe

## Systems search focus

- `mut.topology.single_to_pev_v1`
- `mut.policy.shadow_memory_enable_v1`

## Required tranche outputs

- campaign-round records
- policy snapshots
- provider telemetry
- matched-budget comparisons
- round-level operator EV
- round-level topology EV
- prior-stability tracking
- strongest-family summary
- replay checks for the strongest family per primary lane

## Hard boundary

This tranche does not authorize:

- component promotion
- transfer claims
- broad lane expansion
- async/distributed work
- public comparative claims
