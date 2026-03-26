# DARWIN Stage-5 Transfer Comparability Note

Date: 2026-03-25
Status: supporting note

## Retained transfer

- `transfer.stage5.repo_swe.topology_to_systems.v0` remains the only retained bounded transfer
- the retained claim remains lane-specific and comparison-bounded
- this is not treated as broad transfer learning

## Failed or invalid transfers

- `transfer.stage5.systems.policy_to_scheduling.v0` is `invalid` because of `evaluator_scope_mismatch`
- `transfer.stage5.repo_swe.tool_scope_to_systems.v0` is `failed` because the source family is not active in current Stage-5 proving scope

## Interpretation rule

Stage-5 transfer outcomes are informative only within the current evaluator, budget, family-state, and lane-role constraints.
