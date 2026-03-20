# DARWIN Stage-4 SearchPolicy Review

Date: 2026-03-20
Status: review completed after the first real repo_swe provider-backed pilot run
References:
- `docs/darwin_stage4_live_economics_slice_2026-03-20.md`
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
- provider usage telemetry was returned on every repo_swe live arm
- scaffold rows remain non-claim-eligible on the watchdog lane

## What is not yet validated

- live-provider cost semantics on repo_swe
- live claim eligibility on any Stage-4 arm
- SearchPolicy behavior under provider-priced cost pressure
- valid matched-budget comparisons for topology and tool-scope mutations under the current support-envelope rule

## Current invalidity picture

The first live repo_swe run exposed two exact invalidity classes:

- `support_envelope_digest_mismatch` on topology and tool-scope mutations
- `budget_class_mismatch` on budget-class mutations

## Conclusion

The `SearchPolicyV1` pilot has crossed the live-execution boundary, but it has not yet crossed the live-claim boundary.
