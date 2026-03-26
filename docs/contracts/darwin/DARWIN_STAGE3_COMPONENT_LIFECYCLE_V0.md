# DARWIN Stage-3 Component Lifecycle V0

Date: 2026-03-19
Status: active for the Stage-3 comparative consolidation tranche

## Lifecycle states

- `promotion_ready`
- `promoted`
- `transfer_candidate`
- `withheld`
- `not_promoted`
- `failed_transfer_source`

## Meaning

A family is `promotion_ready` when bounded repeated evidence, evaluator closure, and replay posture make promotion eligible.

A family is `promoted` when that eligibility is accepted into Stage-3 decision truth.

A family is `transfer_candidate` when it is interesting enough for bounded transfer consideration but not yet canonical promotion truth.

A family is `withheld` when it is close but not strong enough to promote.

A family is `not_promoted` when Stage 3 explicitly records that it is outside the current promotion boundary.

A family is `failed_transfer_source` when it remains useful primarily as a failure-boundary example.

## Tranche note

This lifecycle stays narrow. It does not introduce broad supersession machinery or archive-as-truth behavior.
