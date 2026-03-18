# DARWIN Cost Accounting Classification V0

This document defines the additive cost-accounting labels used by the Phase-2 compute-normalized tranche.

## Purpose

DARWIN must distinguish:

- exact local accounting
- estimated local accounting
- unavailable accounting

without implying stronger billing precision than the underlying artifacts support.

## Classes

### `exact_local_zero`

Use when:

- the `EvaluationRecord` carries a numeric local cost field
- that field is exactly `0.0`

Interpretation:

- the evaluator path used no billable local-cost amount under current accounting
- score-per-USD style normalization is intentionally omitted

### `exact_local_nonzero`

Use when:

- the `EvaluationRecord` carries a numeric local cost field
- that field is greater than `0.0`

Interpretation:

- local-cost normalization may be computed

### `estimated_local`

Use when:

- cost is locally inferred or approximated rather than recorded directly

Interpretation:

- comparison is descriptive only
- claim-bearing use requires explicit caution

### `unavailable`

Use when:

- no reliable local cost value exists

Interpretation:

- cost-normalized comparison must be omitted

## External billing

Unless a later tranche lands a provider-authoritative billing surface, external billing classification remains:

- `unavailable`

and must not be presented as exact or estimated local accounting.
