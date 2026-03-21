# DARWIN Stage-5 Secondary-Lane Status

Date: 2026-03-21
Status: bounded Systems compounding slice landed
References:
- `docs/darwin_stage5_secondary_lane_slice_2026-03-21.md`
- `artifacts/darwin/stage5/tranche1/lane_systems/compounding_pilot_v0.json`
- `artifacts/darwin/stage5/tranche1/lane_systems/compounding_cases_v1.json`

## What landed

- `SearchPolicyV2` now supports:
  - `lane.repo_swe`
  - `lane.systems`
- Systems now has a bounded Stage-5 compounding pilot under:
  - `cold_start`
  - `warm_start`
  - `family_lockout`
- the systems pilot binds to the promoted Systems policy family:
  - `component_family.stage4.policy.policy.shadow_memory_enable_v1.lane.systems.v0`

## Current systems result

- `4` arms
- `16` runs
- `8` valid claim-bearing comparisons
- `4` `CompoundingCaseV1` rows
- `1` `reuse_lift`
- `3` `no_lift`

## Interpretation

Systems now participates in the Stage-5 compounding protocol as a bounded secondary lane. The current result is mixed, but it is sufficient to show that family-aware warm-start vs family-lockout attribution is no longer Repo_SWE-only.

This slice does not prove stable multi-lane compounding. It does prove that Stage-5 can run the same typed compounding comparison on both primary lanes without widening runtime truth.

## Route/economics note

OpenRouter remains preferred in code. In the current workspace, Systems live rows still show mixed provider resolution with direct OpenAI fallback after `openrouter_http_401`.

## What this authorizes next

- continue Stage-5 family-aware search with Repo_SWE primary and Systems secondary
- write the next Stage-5 operational tranche around repeated multi-lane compounding
- do not widen into transfer or composition work yet
