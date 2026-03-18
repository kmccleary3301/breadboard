# DARWIN Compute-Normalized Scorecard V2

This document defines the Phase-2 additive compute-normalized comparative surface.

## Scope

The v2 normalized view is still internal-only. It exists to improve comparative interpretation over existing DARWIN artifacts without changing runtime truth.

It records, per lane:

- baseline score and active score
- exact evaluator wall-clock runtime and runtime-normalized score
- local cost-accounting classification
- local-cost-normalized score only when a non-zero local cost exists
- explicit comparison status and interpretation flags

## What changed from v1

Compared with `DARWIN_COMPUTE_NORMALIZED_SCORECARD_V1.md`, this surface adds:

- explicit runtime normalization semantics
- explicit local cost-accounting classification
- explicit external billing unavailability
- per-lane interpretation notes
- clearer comparison-status fields

## Normalization semantics

### Runtime-normalized score

Runtime-normalized score is:

- `primary_score / evaluator_wall_clock_seconds`

This is exact only for the evaluator runtime recorded in the current `EvaluationRecord`.

It is not a full-system throughput metric.

### Local-cost-normalized score

Local-cost-normalized score is:

- `primary_score / local_cost_usd`

This value is only present when a non-zero local cost exists in the underlying evaluation artifact.

If local cost is `0.0`, the normalized score per USD is intentionally `null`.

## Cost-accounting interpretation

The v2 normalized view must classify cost fields as one of:

- `exact_local_zero`
- `exact_local_nonzero`
- `estimated_local`
- `unavailable`

For the current DARWIN local evaluator stack, cost is generally:

- local-only
- evaluator-side
- often exact zero

The view must also carry the explicit statement that external provider billing is unavailable unless a later tranche adds it.

## Limits

- not a superiority surface
- not a publication-grade comparative report
- not a substitute for the invalid-comparison ledger
- not a substitute for replay requirements on promotion or transfer claims

## Canonical artifact for this tranche

- `artifacts/darwin/scorecards/compute_normalized_view_v2.json`
