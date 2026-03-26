# DARWIN Stage-4 Deep Live Search Status

Date: 2026-03-20
Status: complete for bounded deep-live-search purpose
References:
- `docs/darwin_stage4_deep_live_search_execution_plan_2026-03-20.md`
- `artifacts/darwin/stage4/deep_live_search/deep_live_search_v0.json`
- `artifacts/darwin/stage4/deep_live_search/operator_ev_by_round_v0.json`
- `artifacts/darwin/stage4/deep_live_search/topology_ev_by_round_v0.json`

## Summary

- primary lanes covered: `2`
- campaign rounds completed: `4`
- total runs: `37`
- claim-eligible comparisons: `25`
- positive power signals: `15`

## Repo_SWE

- round count: `2`
- valid comparisons by round: `4`, `6`
- strongest families:
  - `mut.topology.single_to_pev_v1`
  - `mut.tool_scope.add_git_diff_v1`
- budget-class mutation remains intentionally invalid via `budget_class_mismatch`

## Systems

- round count: `2`
- valid comparisons by round: `4`, `6`
- strongest families:
  - `mut.policy.shadow_memory_enable_v1`
  - `mut.topology.single_to_pev_v1`

## Interpretation

Stage 4 now has repeated live campaigns on both primary lanes under the same claim-eligibility, evaluator, and comparison-envelope rules.

This is enough to move Stage 4 past pilot-only live evidence.
