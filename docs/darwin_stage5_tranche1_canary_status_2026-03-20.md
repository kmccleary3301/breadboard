# DARWIN Stage-5 Tranche-1 Canary Status

Date: 2026-03-20
Status: tranche-1 canary landed
References:
- `docs/darwin_stage5_tranche1_slice_2026-03-20.md`
- `docs/darwin_stage5_execution_plan_2026-03-20.md`
- `breadboard_ext/darwin/stage4.py`
- `breadboard_ext/darwin/stage5.py`
- `scripts/run_darwin_stage5_compounding_pilot_v0.py`

## What landed

- provider-origin and fallback-reason truth now rides on claim-bearing Stage-5 live rows
- Repo_SWE now has typed `cold_start`, `warm_start`, and `family_lockout` comparison modes
- `SearchPolicyV2` now consumes current promoted family state on `lane.repo_swe`
- `CompoundingCaseV1` now emits from matched warm-start vs family-lockout comparisons
- Stage-5 compounding pilot artifacts now land under `artifacts/darwin/stage5/tranche1/`

## Current observed behavior

- the first Stage-5 pilot runs in scaffold and live mode without widening runtime truth
- the pilot selects one Repo_SWE promoted topology family and emits:
  - one cold-start control arm
  - one warm-start family-enabled arm
  - one family-lockout arm
  - one harness watchdog arm
- live provider calls remain OpenRouter-preferred in code but fall back to direct OpenAI in this workspace after `openrouter_http_401`
- the current canary produces valid claim-bearing warm-start and family-lockout comparisons, but the first two `CompoundingCaseV1` rows conclude `no_lift`

## Important boundary

This slice proves that the compounding protocol is now typed and executable. It does not yet prove positive compounding lift.

The current result is stronger than a schema-only canary because live rows, matched comparisons, and compounding cases are all real. It is still only a tranche-1 protocol result.

## What this authorizes next

- a Stage-5 tranche-1 review and hard gate for Repo_SWE economics truth and compounding protocol
- denser warm-start vs family-lockout repetitions on Repo_SWE
- first decision on whether Systems may enter Stage-5 family-aware search beyond canary scope
