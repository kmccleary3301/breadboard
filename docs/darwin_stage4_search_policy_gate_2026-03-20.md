# DARWIN Stage-4 SearchPolicy Gate

Date: 2026-03-20
Status: blocked after the first real live-provider pilot
References:
- `docs/darwin_stage4_search_policy_review_2026-03-20.md`
- `docs/darwin_stage4_live_readiness_2026-03-20.md`
- `artifacts/darwin/stage4/live_economics/live_readiness_v0.json`

## Gate question

Can Stage 4 now count repo_swe SearchPolicy pilot outputs as live-provider power evidence?

## Decision

No.

## Why the gate is blocked

- Stage-4 worker pricing inputs are not present, so live rows remain `cost_source=provider_usage_only`
- topology and tool-scope mutations currently fail the matched-budget gate via `support_envelope_digest_mismatch`
- budget mutations currently fail the matched-budget gate via `budget_class_mismatch`

## What is authorized

- treat the current repo_swe live pilot as a valid execution/readiness result
- add Stage-4 mini pricing inputs and rerun the live-economics pilot
- keep the current repo_swe operator set for the next run
- decide in the next slice whether topology/tool-scope mutations should preserve or normalize the support-envelope comparison boundary

## What is not authorized

- counting the current live rows as Stage-4 power evidence
- claiming matched-budget success from the current invalid comparison set
- widening the SearchPolicy pilot to other lanes before repo_swe live claim eligibility is real
