# DARWIN Stage-4 Tranche-1 Slice

Date: 2026-03-19
Status: initial Stage-4 tranche-1 implementation slice
References:
- `docs/darwin_stage4_doctrine_2026-03-19.md`
- `docs/darwin_stage4_execution_plan_2026-03-19.md`
- `docs/darwin_stage4_adr_set_2026-03-19.md`

## Purpose

This document defines the smallest useful Stage-4 tranche-1 implementation slice.

The slice is intentionally narrow. It does not attempt to make Stage 4 live-provider capable by itself. It hardens the boundaries and telemetry needed before real live-provider claims are allowed.

## Slice thesis

> make live vs scaffold execution explicit, harden budget/comparison semantics, and deepen `ExecutionEnvelopeV2` closure on authorized paths without changing runtime ownership.

## Included work

1. live/scaffold boundary enforcement on Stage-4 campaign surfaces
2. provider-telemetry-ready budget envelope semantics
3. tighter matched-budget comparison contract
4. first `ExecutionEnvelopeV2` read path on authorized lanes

## Excluded work

- actual provider-calling search execution
- broad campaign expansion
- `SearchPolicyV1` operational rollout
- multi-family promotion work
- transfer-matrix expansion

## Authorized proving surfaces

- primary: `lane.repo_swe`
- watchdog/control: `lane.harness`
- systems canary: `lane.systems`
- audit: `lane.atp`

## Hard gate for this slice

- scaffold-mode runs are never treated as live power evidence
- claim-bearing route/budget metadata is materially tighter than Stage 3
- `ExecutionEnvelopeV2` closure exists on authorized lanes without replay/parity drift
- provider-backed live campaigns remain blocked until a real provider-calling path exists
