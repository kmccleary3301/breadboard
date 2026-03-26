# DARWIN Stage-4 Comparison-Envelope Slice

Date: 2026-03-20
Status: landed
References:
- `docs/darwin_stage4_live_economics_slice_2026-03-20.md`
- `breadboard_ext/darwin/stage4.py`
- `scripts/build_darwin_stage4_matched_budget_view_v1.py`

## Purpose

This slice narrows the Stage-4 comparison contract after the first live repo_swe pilot.

The original Stage-4 support-envelope digest was too strict for topology and tool-scope mutation comparisons. It was encoding the mutated surface itself, which made those comparisons invalid by construction.

## What changed

- cached-input pricing support is now modeled explicitly for Stage-4 provider-backed runs
- live pricing envs now include cached-input cost
- Stage-4 comparison validity now uses a normalized comparison envelope instead of the full support envelope
- topology and tool-scope mutations can now participate in valid matched-budget comparisons when lane, evaluator family, budget class, and environment invariants still match
- budget-class mutations remain invalid under matched-budget comparison by design

## Boundary

- the full `support_envelope_digest` remains emitted for support and audit inspection
- the normalized comparison envelope is only for matched-budget comparison validity
- this slice does not widen the operator set
- this slice does not widen lanes
- this slice does not claim positive Stage-4 power evidence on its own

## Result

The repo_swe live pilot now has:

- live claim-eligible rows
- valid topology and tool-scope matched-budget comparisons
- budget-class mismatches isolated as the remaining designed invalidity class

What it still does not have is positive expected-value signal. That remains the next Stage-4 problem.
