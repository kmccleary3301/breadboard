# DARWIN Stage-4 SearchPolicy Gate

Date: 2026-03-20
Status: blocked
References:
- `docs/darwin_stage4_search_policy_review_2026-03-20.md`
- `docs/darwin_stage4_live_readiness_2026-03-20.md`
- `artifacts/darwin/stage4/live_economics/live_readiness_v0.json`

## Gate question

Can Stage 4 now count repo_swe SearchPolicy pilot outputs as live-provider power evidence?

## Decision

No.

## Why the gate is blocked

- no provider credentials are present in the workspace
- live execution has not been explicitly enabled
- Stage-4 worker pricing inputs are not present

## What is authorized

- keep the current scaffold-mode SearchPolicy pilot as a valid structural/control surface
- rerun the Stage-4 live-economics pilot once provider and pricing inputs are present
- reuse the existing repo_swe operator set for the first live pilot

## What is not authorized

- counting scaffold rows as Stage-4 power evidence
- writing live-economics conclusions from the current workspace state
- widening the SearchPolicy pilot to other lanes before repo_swe live validation
