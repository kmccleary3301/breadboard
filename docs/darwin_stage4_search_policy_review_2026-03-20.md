# DARWIN Stage-4 SearchPolicy Review

Date: 2026-03-20
Status: review updated after repo_swe EV refinement
References:
- `docs/darwin_stage4_live_economics_slice_2026-03-20.md`
- `docs/darwin_stage4_comparison_envelope_slice_2026-03-20.md`
- `docs/darwin_stage4_repo_swe_ev_refinement_slice_2026-03-20.md`
- `docs/darwin_stage4_live_readiness_2026-03-20.md`
- `artifacts/darwin/stage4/live_economics/live_economics_pilot_v0.json`
- `artifacts/darwin/stage4/live_economics/search_policy_v1.json`
- `artifacts/darwin/stage4/live_economics/operator_ev_report_v0.json`
- `artifacts/darwin/stage4/live_economics/topology_ev_report_v0.json`

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
- repo_swe live comparisons now pair against matching control repetitions
- topology and tool-scope families now show bounded positive power signals
- scaffold rows remain non-claim-eligible on the watchdog lane

## What is not yet validated

- SearchPolicy behavior under meaningful live improvement pressure
- systems-lane live-provider behavior
- any broad power claim beyond the bounded repo_swe slice

## Current invalidity picture

The current repo_swe live run now isolates one designed invalidity class:

- `budget_class_mismatch` on budget-class mutations

## Conclusion

The `SearchPolicyV1` pilot has crossed the live-claim boundary and now carries bounded repo_swe power evidence.

The current evidence is narrow:

- score remains flat
- topology and tool-scope families improve runtime and/or provider-backed cost under valid live comparisons
- budget mutation remains intentionally unmatched

That is sufficient for a bounded Stage-4 repo_swe power signal.

It is not sufficient for broader compounding claims or systems-lane claims.
