# DARWIN Compute-Normalized Scorecard V1

This document defines the Phase-1 compute-normalized companion view.

## Scope

The compute-normalized companion is internal only. It records:

- baseline score
- active candidate score
- baseline score per second
- active score per second
- score delta
- rate delta

## Limits

- this is not a superiority surface
- local cost is mostly zero-cost evaluator runtime, not external model billing
- comparisons remain only as valid as the underlying lane evaluator and invalid-comparison ledger

## Canonical artifact

- `artifacts/darwin/scorecards/compute_normalized_view_v1.json`
