# DARWIN Stage-4 Live Readiness

Date: 2026-03-20
Status: live provider path verified and pricing-ready
References:
- `scripts/build_darwin_stage4_live_readiness_v0.py`
- `artifacts/darwin/stage4/live_economics/live_readiness_v0.json`
- `docs/darwin_stage4_live_economics_status_2026-03-20.md`

## Current state

The Stage-4 repo_swe live-economics pilot exists and the workspace is now eligible for live-provider claims.

## Current blocking condition

None at the environment/readiness layer.

## What this means

- the Stage-4 repo_swe pilot can now execute live provider calls when `.env` is sourced and `DARWIN_STAGE4_ENABLE_LIVE=1`
- cached-input pricing is now part of the Stage-4 cost model
- the readiness artifact is now `ready_for_live_claims=true`
- the SearchPolicy pilot is valid as a bounded live-claim surface, but it is still not yet Stage-4 power evidence

## Current note

OpenRouter remains the preferred route. In this workspace, the current Stage-4 live pilot fell back to direct OpenAI after an OpenRouter `401 Unauthorized` response. The readiness state still counts as provider-ready because a real provider-backed live path is functioning and priced.
