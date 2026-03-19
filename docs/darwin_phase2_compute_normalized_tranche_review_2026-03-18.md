# DARWIN Phase-2 Compute-Normalized Tranche Review

Date: 2026-03-18
Status: interim tranche review
Primary issue: `breadboard_repo_darwin_phase1_20260313-8xc`
Decision: continue current compute-normalized tranche; do not unlock broader transfer families yet

## Scope reviewed

This review covers the current additive-only compute-normalized tranche state:

- stronger normalization policy
- explicit cost-accounting classification
- `compute_normalized_view_v2`
- scorecard integration of the v2 normalized surface
- compute-normalized proving review on the selected proving lanes

## Artifacts reviewed

- `docs/contracts/darwin/DARWIN_COMPUTE_NORMALIZED_SCORECARD_V2.md`
- `docs/contracts/darwin/DARWIN_COST_ACCOUNTING_CLASSIFICATION_V0.md`
- `artifacts/darwin/scorecards/compute_normalized_view_v2.json`
- `artifacts/darwin/scorecards/t1_baseline_scorecard.latest.json`
- `artifacts/darwin/reviews/compute_normalized_review_v0.json`

## What is now true

- runtime-normalized comparison is explicitly evaluator-wall-clock only
- local cost semantics are classified as exact, estimated, or unavailable
- zero-cost local evaluation no longer implies fake score-per-USD precision
- the comparative scorecard now carries comparison status and interpretation flags from the normalized surface
- proving-lane review is explicit rather than implicit

## Proving-lane read

### `lane.harness`

Current read:

- coherent
- baseline retained
- no invalid-comparison pressure
- runtime-normalized fields are descriptive rather than decision-driving because the lane is score-saturated

Interpretation:

- the normalized surface is useful here because it explains why the lane remains a proving lane even when score deltas are flat
- this is the right kind of clarity improvement for this tranche

### `lane.repo_swe`

Current read:

- coherent
- baseline retained with invalid trials present
- invalid-comparison handling is now explicit in both the normalized view and the scorecard

Interpretation:

- this is the strongest current proof point for the tranche
- the improved surface makes it much easier to distinguish a stable retained baseline from a superficially comparable but invalid trial set

## Audit-lane read

### `lane.scheduling`

- promoted candidate movement looks coherent under the additive normalized surface
- still audit-only for this tranche

### `lane.atp`

- normalized fields remain descriptive only
- ATP remains an audit lane and should not become the proving center for this tranche

## Decision

The compute-normalized tranche is progressing correctly, but it is **not yet** strong enough to unlock the later broader transfer-family / lineage-policy tranche.

The reason is straightforward:

- the current normalized surface is now materially better
- but the tranche has not yet produced a sufficiently strong comparative memo/review layer for downstream transfer-policy decisions
- and it has not yet shown enough decision-quality improvement over the current proving lanes to justify widening semantic scope

So the correct current decision is:

- continue the compute-normalized tranche
- keep the work additive-only
- keep broader transfer-family work deferred

## What remains before the later transfer/lineage tranche should be reconsidered

- stronger comparative memo synthesis using the new scorecard + normalized surface together
- clearer proving-lane narrative for why `lane.harness` and `lane.repo_swe` are semantically clearer now than they were under the Phase-1 companion
- one more review pass confirming the normalized surfaces are decision-useful, not merely better documented

## Boundary reaffirmed

- no runtime consumption of tranche-1 compiled artifacts
- no new BreadBoard kernel-truth primitive
- no broader transfer-family rollout yet
- no external-safe evidence expansion yet

## Read

This tranche is working.

It has improved comparative rigor.

But it has not yet crossed the threshold where broader transfer-family and lineage-policy expansion would be the highest-value next move.
