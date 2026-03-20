# DARWIN Stage-4 Live-Economics Status

Date: 2026-03-20
Status: repo_swe EV refinement landed on top of live pricing semantics and normalized comparison-envelope work
References:
- `docs/darwin_stage4_live_economics_slice_2026-03-20.md`
- `docs/darwin_stage4_comparison_envelope_slice_2026-03-20.md`
- `docs/darwin_stage4_repo_swe_ev_refinement_slice_2026-03-20.md`
- `docs/darwin_stage4_systems_live_slice_2026-03-20.md`
- `docs/darwin_stage4_systems_live_readiness_2026-03-20.md`
- `docs/darwin_stage4_cross_primary_lane_review_2026-03-20.md`
- `docs/darwin_stage4_deep_live_search_gate_2026-03-20.md`
- `scripts/run_darwin_stage4_live_economics_pilot_v0.py`
- `scripts/run_darwin_stage4_systems_live_pilot_v0.py`
- `scripts/build_darwin_stage4_matched_budget_view_v1.py`
- `scripts/build_darwin_stage4_operator_ev_report_v0.py`
- `scripts/build_darwin_stage4_topology_ev_report_v0.py`
- `scripts/build_darwin_stage4_systems_ev_bundle_v0.py`
- `breadboard_ext/darwin/stage4.py`

## What landed

- explicit Stage-4 live execution opt-in gate via `DARWIN_STAGE4_ENABLE_LIVE`
- provider-backed telemetry helper path in `breadboard_ext/darwin/stage4.py`
- cached-input pricing support for Stage-4 provider-backed runs
- claim eligibility only after real provider calls plus provider-backed cost semantics
- first operational `SearchPolicyV1` pilot on `lane.repo_swe`
- normalized comparison-envelope semantics for topology and tool-scope mutations
- repo_swe EV refinement with repetition-matched controls and explicit power-signal classification
- first narrow systems live-provider pilot
- systems EV bundle with matched-budget, operator EV, and topology EV outputs
- additive Stage-4 live-economics pilot artifacts under `artifacts/darwin/stage4/live_economics/`

## Current workspace behavior

The default shell state is still scaffold-mode unless:

- `DARWIN_STAGE4_ENABLE_LIVE=1`
- `OPENAI_API_KEY` or `OPENROUTER_API_KEY` is present
- the lane is explicitly authorized for live execution

Using the repo-root `.env` plus `DARWIN_STAGE4_ENABLE_LIVE=1`, the Stage-4 repo_swe pilot now executes real provider-backed proposal calls.

OpenRouter remains the preferred default route. In this workspace, the OpenRouter live call returned `401 Unauthorized`, so the Stage-4 pilot now falls back to direct `openai/gpt-5.4-mini` when both provider keys are present.

## What is now true

- scaffold-only rows remain claim-ineligible
- repo_swe rows can now execute with `execution_mode=live`
- repo_swe live rows now have provider-priced cost semantics using:
  - `DARWIN_STAGE4_GPT54_MINI_INPUT_COST_PER_1M`
  - `DARWIN_STAGE4_GPT54_MINI_CACHED_INPUT_COST_PER_1M`
  - `DARWIN_STAGE4_GPT54_MINI_OUTPUT_COST_PER_1M`
- `SearchPolicyV1` operationally selects the `lane.repo_swe` mutation arm set
- topology and tool-scope mutations now produce valid matched-budget comparisons under the normalized comparison envelope
- topology and tool-scope families now show bounded positive power signals through retained-score runtime/cost improvement
- budget-class mutations remain invalid under matched-budget comparison, as intended
- repo_swe Stage-4 pilot artifacts remain distinct from Stage-3 bounded-inference artifacts
- systems rows can now execute with `execution_mode=live`
- systems live rows now have provider-priced cost semantics
- systems topology and policy families now produce valid matched-budget live comparisons
- systems now has bounded positive power signals through retained-score cost improvement
- Stage-4 has crossed the live-claim boundary on both primary lanes and has bounded power evidence on both

## Next step

The next justified move is deeper bounded live search on `lane.repo_swe` and `lane.systems`.
