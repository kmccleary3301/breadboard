# DARWIN Stage-3 Component Promotion Policy V0

Date: 2026-03-19
Status: active for the Stage-3 component-promotion tranche

## Purpose

Define when a Stage-3 component family is promoted, withheld, or rejected.

## Decision classes

- `promoted`
- `withheld`
- `not_promoted`
- `descriptive_only`

## Promotion-ready minimum

A component family is promotion-ready only when:

- valid comparison count is repeated and non-trivial
- matched-budget retention remains intact
- positive expected value is visible under the tranche rules
- replay posture is recorded
- the decision can be expressed in `DecisionLedgerV1`

## Candidate win versus component-family win

A candidate win is a single bounded run outcome.

A component-family win is a reusable typed family that survives repeated bounded comparisons and is appropriate for bounded transfer consideration.

## Replay rule

Promotion is replay-backed. If replay support is missing or weak, the family must be downgraded to `withheld` or `descriptive_only`.

## Supersession note

This tranche does not introduce broad supersession chains. It only records the first promotion and the first explicit non-promotion outcome.
