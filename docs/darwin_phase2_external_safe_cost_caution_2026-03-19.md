# DARWIN External-Safe Cost Caution Note

Date: 2026-03-19
Status: active caution note for the external-safe evidence tranche
Primary issue: `breadboard_repo_darwin_phase1_20260313-ovi`

## Rule

The external-safe packet may reference compute-normalized and local-cost surfaces only with the current DARWIN caution language intact.

## Exact / estimated / unavailable

- exact local classes:
  - `exact_local_zero`
  - `exact_local_nonzero`
- estimated local class:
  - `estimated_local`
- unavailable or non-comparable classes:
  - `external_billing_unavailable`
  - `local_cost_denominator_zero`

## Non-overread rule

Do not infer per-dollar superiority when:

- external billing is unavailable
- the local denominator is zero
- the local denominator is only estimated

## Scope

This note exists to keep the external-safe packet honest.

It does not introduce any new cost-accounting mechanism.
