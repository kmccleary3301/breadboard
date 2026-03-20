# DARWIN Stage-4 SearchPolicy Review

Date: 2026-03-20
Status: review completed with live gate blocked
References:
- `docs/darwin_stage4_live_economics_slice_2026-03-20.md`
- `docs/darwin_stage4_live_readiness_2026-03-20.md`
- `artifacts/darwin/stage4/live_economics/live_economics_pilot_v0.json`
- `artifacts/darwin/stage4/live_economics/search_policy_v1.json`

## Review summary

The first narrow `SearchPolicyV1` pilot is behaving correctly at the structural level.

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
- scaffold-mode telemetry is emitted on every selected arm
- scaffold rows remain non-claim-eligible

## What is not yet validated

- live-provider economics on repo_swe
- live claim eligibility on any Stage-4 arm
- SearchPolicy behavior under real provider cost pressure

## Conclusion

The `SearchPolicyV1` pilot is ready to be exercised on live provider-backed repo_swe runs, but the workspace is not currently provider-ready enough to cross that gate.
