# DARWIN Stage-4 Component Promotion Policy V0

Date: 2026-03-20
Status: active for the Stage-4 multi-family promotion tranche

## Purpose

Define when a Stage-4 family becomes `provisional_family`, `promotion_ready`, `promoted`, `withheld`, or `not_promoted`.

## Decision classes

- `provisional_family`
- `promotion_ready`
- `promoted`
- `withheld`
- `not_promoted`

## Promotion-ready minimum

A family is promotion-ready only when:

- valid comparison count is repeated and non-trivial
- positive power signal count is repeated
- invalidity remains controlled
- replay posture is at least recorded
- the decision can be expressed in `DecisionLedgerV1.1`

## Promotion rule

Promotion is replay-backed. If replay support is missing or weak, the family must be downgraded to `withheld`.

## Candidate win versus family win

A candidate win is one live or replayed run outcome.

A family win is a reusable typed family that survives repeated bounded comparisons and is eligible for bounded transfer consideration.
