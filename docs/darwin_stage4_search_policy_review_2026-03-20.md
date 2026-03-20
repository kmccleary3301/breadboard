# DARWIN Stage-4 SearchPolicy Review

Date: 2026-03-20
Status: review updated after cached-pricing support and normalized comparison-envelope landing
References:
- `docs/darwin_stage4_live_economics_slice_2026-03-20.md`
- `docs/darwin_stage4_comparison_envelope_slice_2026-03-20.md`
- `docs/darwin_stage4_live_readiness_2026-03-20.md`
- `artifacts/darwin/stage4/live_economics/live_economics_pilot_v0.json`
- `artifacts/darwin/stage4/live_economics/search_policy_v1.json`

## Review summary

The first narrow `SearchPolicyV1` pilot is now behaving correctly at the structural level and has been exercised through a real provider-backed repo_swe run.

It does three useful things now:

1. keeps the repo_swe arm set bounded
2. picks the highest-priority mutation operators first
3. preserves an explicit matched-budget control arm

## What the pilot selected

The current repo_swe mutation set is:

- `mut.topology.single_to_pev_v1`
- `mut.tool_scope.add_git_diff_v1`
- `mut.budget.class_a_to_class_b_v1`

The lower-priority `mut.policy.shadow_memory_enable_v1` row remains excluded from the first pilot.

## What is validated

- the policy is operational rather than merely documentary
- arm selection is deterministic
- repo_swe selected arms executed with `execution_mode=live`
- provider-backed pricing is now attached to live repo_swe arms
- topology and tool-scope mutations now produce valid matched-budget comparisons
- scaffold rows remain non-claim-eligible on the watchdog lane

## What is not yet validated

- positive expected-value signal on repo_swe
- SearchPolicy behavior under meaningful live improvement pressure
- systems-lane live-provider behavior
- any power claim beyond bounded live-operational readiness

## Current invalidity picture

The current repo_swe live run now isolates one designed invalidity class:

- `budget_class_mismatch` on budget-class mutations

## Conclusion

The `SearchPolicyV1` pilot has crossed the live-claim boundary.

It has not yet crossed the power-evidence boundary, because the current valid comparisons still show `delta_score=0.0` across the live repo_swe mutation set.
