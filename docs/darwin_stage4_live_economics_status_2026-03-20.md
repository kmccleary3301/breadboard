# DARWIN Stage-4 Live-Economics Status

Date: 2026-03-20
Status: landed first live-economics/SearchPolicy pilot slice and executed the first live provider-backed repo_swe run
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

The default shell state is still scaffold-mode unless:

- `DARWIN_STAGE4_ENABLE_LIVE=1`
- `OPENAI_API_KEY` or `OPENROUTER_API_KEY` is present
- the lane is explicitly authorized for live execution

Using the repo-root `.env` plus `DARWIN_STAGE4_ENABLE_LIVE=1`, the Stage-4 repo_swe pilot now executes real provider-backed proposal calls on the default OpenRouter route.

## What is now true

- scaffold-only rows remain claim-ineligible
- repo_swe rows can now execute with `execution_mode=live`
- `SearchPolicyV1` operationally selects the `lane.repo_swe` mutation arm set
- repo_swe Stage-4 pilot artifacts remain distinct from Stage-3 bounded-inference artifacts
- live power claims are still blocked because the current run lacks provider-backed cost semantics and valid matched-budget comparisons

## Next step

The next justified move is to add the Stage-4 mini pricing inputs and then decide whether the current strict support-envelope digest should remain the comparison gate for topology/tool-scope mutations or be normalized for the next Stage-4 slice.
