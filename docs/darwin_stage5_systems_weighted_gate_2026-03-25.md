# DARWIN Stage-5 Systems-Weighted Gate

Date: 2026-03-25
Status: passed
References:
- `docs/darwin_stage5_systems_weighted_status_2026-03-25.md`
- `docs/darwin_stage5_systems_weighted_review_2026-03-25.md`

## Gate checklist

- the cross-lane proving-weight decision is now encoded in active Stage-5 policy
- Systems is explicitly the primary proving lane
- Repo_SWE is explicitly the challenge lane
- Repo_SWE automatic family probing is suppressed while its family A/B surface is stale
- a bounded systems-weighted runner exists
- no transfer, composition, or runtime-truth widening was introduced

## Gate decision

Passed.

## What is authorized next

- a live systems-weighted Stage-5 review run
- continued Repo_SWE participation as a bounded protocol challenge lane
- bounded Repo_SWE cleanup only where needed to keep the challenge lane interpretable

## What is not authorized next

- no transfer-matrix expansion yet
- no family composition yet
- no claim that Stage-5 scalable compounding is already stable
