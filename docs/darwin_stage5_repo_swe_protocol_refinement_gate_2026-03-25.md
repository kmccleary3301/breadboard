# DARWIN Stage-5 Repo_SWE Protocol Refinement Gate

Date: 2026-03-25
Status: passed
References:
- `docs/darwin_stage5_repo_swe_protocol_refinement_status_2026-03-25.md`
- `docs/darwin_stage5_repo_swe_protocol_refinement_review_2026-03-25.md`

## Gate checklist

- Repo_SWE comparison logic no longer overvalues tiny runtime deltas against material cost changes
- the refined rule was exercised on the live Repo_SWE family A/B surface
- the Repo_SWE family choice remained stable under the stronger rule

## Gate decision

Passed.

## What is authorized next

- keep Repo_SWE on `tool_scope`
- refine the protocol around the selected family
- keep Systems unchanged unless a cross-lane review forces a change

## What is not authorized next

- no return to topology as the default Repo_SWE family
- no transfer expansion
- no family composition
