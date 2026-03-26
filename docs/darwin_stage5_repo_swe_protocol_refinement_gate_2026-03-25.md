# DARWIN Stage-5 Repo_SWE Protocol Refinement Gate

Date: 2026-03-25
Status: passed
References:
- `docs/darwin_stage5_repo_swe_protocol_refinement_status_2026-03-25.md`
- `docs/darwin_stage5_repo_swe_protocol_refinement_review_2026-03-25.md`

## Gate checklist

- Repo_SWE comparison logic no longer overvalues tiny runtime deltas against material cost changes
- the refined rule was exercised on the live Repo_SWE family A/B surface
- the refined rule exposed that the Repo_SWE family choice is not stable under stronger comparison semantics

## Gate decision

Passed.

## What is authorized next

- stop treating the Repo_SWE family decision as settled
- run a bounded cross-lane Stage-5 review next
- keep Systems unchanged until that review is written

## What is not authorized next

- no claim that Repo_SWE family selection is now solved
- no transfer expansion
- no family composition
