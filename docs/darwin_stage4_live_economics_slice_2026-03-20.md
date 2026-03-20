# DARWIN Stage-4 Live-Economics Slice

Date: 2026-03-20
Status: active Stage-4 tranche-1 implementation slice
References:
- `docs/darwin_stage4_execution_plan_2026-03-19.md`
- `docs/darwin_stage4_tranche1_slice_2026-03-19.md`
- `docs/darwin_stage4_tranche1_canary_status_2026-03-19.md`

## Purpose

This slice advances Stage 4 from the canary boundary into the first live-economics-ready path.

It does not authorize broad live-provider search. It adds the minimum honest infrastructure needed to separate scaffold execution from real provider-backed search work.

## Slice thesis

> wire a provider-backed telemetry path, enforce live claim eligibility on actual provider calls only, and make `SearchPolicyV1` operational on a narrow `lane.repo_swe` pilot.

## Included work

1. explicit live-provider opt-in gate
2. provider-backed telemetry rows for the Stage-4 pilot path
3. claim eligibility derived from real provider call metadata
4. narrow `SearchPolicyV1` pilot on `lane.repo_swe`
5. additive Stage-4 live-economics artifact emission

## Excluded work

- broad live campaigns
- systems live-provider expansion
- multi-family promotion work
- transfer-matrix expansion
- any widening of runtime truth

## Authorized proving surfaces

- primary: `lane.repo_swe`
- watchdog/control: `lane.harness`
- audit: `lane.atp`

## Hard gate for this slice

- live execution requires explicit opt-in and real provider readiness
- scaffold rows never become claim-eligible
- provider telemetry is recorded on every live-eligible arm
- `SearchPolicyV1` selects `lane.repo_swe` mutation arms operationally
- runtime truth remains singular and locally executed
