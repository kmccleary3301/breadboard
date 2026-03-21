# DARWIN Stage-5 Tranche-1 Hard Gate

Date: 2026-03-21
Status: passed
References:
- `docs/darwin_stage5_tranche1_slice_2026-03-20.md`
- `docs/darwin_stage5_tranche1_status_2026-03-21.md`
- `docs/darwin_stage5_tranche1_review_2026-03-21.md`

## Gate checklist

- provider-origin, fallback-reason, and cost-source truth is canonical on claim-bearing live rows
- `cold_start`, `warm_start`, and `family_lockout` are executable on Repo_SWE without runtime-truth expansion
- matched live comparisons and `CompoundingCaseV1` rows emit cleanly
- Repo_SWE family-aware search is typed through `SearchPolicyV2`
- denser repetitions now show mixed positive and non-positive compounding outcomes instead of schema-only output
- no Stage-4 live-claim boundary or BR4 runtime ownership regressions were introduced

## Gate decision

Passed.

## What is authorized next

- keep Repo_SWE as the Stage-5 primary lane
- allow Systems to enter the next Stage-5 slice as a bounded secondary lane
- widen Stage-5 family-aware search only one notch beyond tranche-1 protocol density

## What is not authorized next

- no broad Stage-5 transfer matrix
- no claim of stable scalable compounding
- no broad lane rollout
