# DARWIN Stage-4 Live-Economics Status

Date: 2026-03-20
Status: landed first live-economics/SearchPolicy pilot slice
References:
- `docs/darwin_stage4_live_economics_slice_2026-03-20.md`
- `scripts/run_darwin_stage4_live_economics_pilot_v0.py`
- `breadboard_ext/darwin/stage4.py`

## What landed

- explicit Stage-4 live execution opt-in gate via `DARWIN_STAGE4_ENABLE_LIVE`
- provider-backed telemetry helper path in `breadboard_ext/darwin/stage4.py`
- claim eligibility only after real provider calls plus provider-backed cost semantics
- first operational `SearchPolicyV1` pilot on `lane.repo_swe`
- additive Stage-4 live-economics pilot artifacts under `artifacts/darwin/stage4/live_economics/`

## Current workspace behavior

In this workspace, the new pilot remains scaffold-mode unless:

- `DARWIN_STAGE4_ENABLE_LIVE=1`
- `OPENAI_API_KEY` or `OPENROUTER_API_KEY` is present
- the lane is explicitly authorized for live execution

This keeps Stage-4 power claims honest while still exercising the live-economics path in scaffold mode.

## What is now true

- scaffold-only rows remain claim-ineligible
- live claim eligibility depends on an actual provider call
- `SearchPolicyV1` now operationally selects the `lane.repo_swe` mutation arm set
- repo_swe Stage-4 pilot artifacts are now distinct from Stage-3 bounded-inference artifacts

## Next step

The next justified move is the first real provider-backed pilot run on `lane.repo_swe` when credentials and pricing inputs are available, followed by the first narrow `SearchPolicyV1` review/gate.
