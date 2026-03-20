# DARWIN Stage-4 SearchPolicy Gate

Date: 2026-03-20
Status: partially unblocked after pricing support and normalized comparison-envelope landing
References:
- `docs/darwin_stage4_search_policy_review_2026-03-20.md`
- `docs/darwin_stage4_live_readiness_2026-03-20.md`
- `artifacts/darwin/stage4/live_economics/live_readiness_v0.json`

## Gate question

Can Stage 4 now count repo_swe SearchPolicy pilot outputs as live-provider power evidence?

## Decision

No.

## What is now satisfied

- Stage-4 worker pricing inputs are present
- cached-input pricing is modeled
- the workspace is `ready_for_live_claims=true`
- topology and tool-scope mutations now pass matched-budget comparison validity

## Why the gate is still blocked

- current valid live comparisons still show `delta_score=0.0`
- budget mutations still fail matched-budget comparison via `budget_class_mismatch`
- there is still no positive repo_swe live expected-value signal

## What is authorized

- treat the current repo_swe live pilot as a valid live-claim/readiness result
- keep the current repo_swe operator set for the next bounded slice
- use topology and tool-scope families as valid matched-budget comparison families
- keep budget-class mutation as intentionally unmatched
- decide whether the next bounded Stage-4 slice is:
  - repo_swe EV refinement
  - or first narrow systems live-provider expansion

## What is not authorized

- counting zero-delta live rows as Stage-4 power evidence
- treating budget-class mutation as matched-budget valid
- widening Stage-4 into broad live campaigns from this pilot alone
