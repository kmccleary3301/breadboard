# DARWIN Stage-4 Repo_SWE EV Refinement Slice

Date: 2026-03-20
Status: completed bounded refinement slice
Scope: tighten repo_swe live matched-budget comparisons into an explicit expected-value surface
References:
- `docs/darwin_stage4_live_economics_slice_2026-03-20.md`
- `docs/darwin_stage4_comparison_envelope_slice_2026-03-20.md`
- `scripts/run_darwin_stage4_live_economics_pilot_v0.py`
- `scripts/build_darwin_stage4_operator_ev_report_v0.py`
- `scripts/build_darwin_stage4_topology_ev_report_v0.py`
- `artifacts/darwin/stage4/live_economics/matched_budget_comparisons_v0.json`
- `artifacts/darwin/stage4/live_economics/operator_ev_report_v0.json`
- `artifacts/darwin/stage4/live_economics/topology_ev_report_v0.json`

## Purpose

The earlier Stage-4 live-economics slice established a real live-provider boundary, but its comparison layer remained too weak for expected-value judgments.

This refinement slice closes that gap without widening the tranche boundary.

## What changed

1. matched-budget comparisons now pair mutation rows against the same control repetition when available
2. Stage-4 comparisons now classify bounded power signals explicitly
3. operator-level and topology-level EV reports now exist for the live repo_swe pilot

## Positive-signal rule

For this bounded repo_swe slice, a comparison counts as a positive Stage-4 power signal only when:

- the comparison is matched-budget valid
- the row is live-claim-eligible
- and one of the following holds:
  - score improves
  - score is retained while runtime improves
  - score is retained while provider-backed cost improves

This does not authorize broad performance claims.

It does authorize bounded efficiency-preserving power evidence on repo_swe.

## Current result

The live repo_swe pilot now shows:

- positive topology-family signal under matched-budget live comparison
- positive tool-scope-family signal under matched-budget live comparison
- budget-class mutation still invalid by design under matched-budget rules

## Boundary

This slice does not:

- claim broad compounding yet
- claim score-improving live search
- claim systems-lane power evidence
- widen Stage 4 into broad live campaigns
