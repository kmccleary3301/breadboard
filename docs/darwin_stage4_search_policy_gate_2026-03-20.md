# DARWIN Stage-4 SearchPolicy Gate

Date: 2026-03-20
Status: repo_swe gate passed; superseded for tranche progression by the deep-live-search gate
References:
- `docs/darwin_stage4_search_policy_review_2026-03-20.md`
- `docs/darwin_stage4_live_readiness_2026-03-20.md`
- `artifacts/darwin/stage4/live_economics/live_readiness_v0.json`
- `docs/darwin_stage4_deep_live_search_gate_2026-03-20.md`

## Gate question

Can Stage 4 now count repo_swe SearchPolicy pilot outputs as live-provider power evidence?

## Decision

Yes, in a bounded repo_swe sense.

## What is now satisfied

- Stage-4 worker pricing inputs are present
- cached-input pricing is modeled
- the workspace is `ready_for_live_claims=true`
- topology and tool-scope mutations now pass matched-budget comparison validity
- repo_swe comparisons now pair against matching control repetitions
- topology and tool-scope families now show positive power signals through retained-score runtime/cost improvement

## Why the gate is still blocked

- score does not improve in the current pilot
- budget mutations still fail matched-budget comparison via `budget_class_mismatch`
- systems-lane live-provider behavior is still unproven

## What is authorized

- treat the current repo_swe live pilot as a bounded live-provider power result
- keep the current repo_swe operator set for the next bounded slice
- use topology and tool-scope families as valid matched-budget comparison families
- keep budget-class mutation as intentionally unmatched
- the first narrow systems live-provider expansion, now landed
- the cross-primary-lane gate as the active Stage-4 entrypoint for deeper live search

## What is not authorized

- counting flat-score rows as broad Stage-4 compounding evidence without the bounded efficiency rule
- treating budget-class mutation as matched-budget valid
- widening Stage-4 into broad live campaigns from this pilot alone
