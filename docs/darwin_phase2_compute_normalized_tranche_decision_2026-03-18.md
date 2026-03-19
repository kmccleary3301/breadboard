# DARWIN Phase-2 Compute-Normalized Tranche Decision

Date: 2026-03-18
Status: follow-up review complete
Primary issue: `breadboard_repo_darwin_phase1_20260313-8xc`
Decision: compute-normalized tranche is now strong enough to unlock the later transfer/lineage tranche

## Scope reviewed

This follow-up review considers the full current compute-normalized tranche state:

- normalization policy
- cost-accounting classification
- `compute_normalized_view_v2`
- scorecard integration
- proving review
- comparative memo synthesis

## Artifacts reviewed

- `docs/darwin_phase2_compute_normalized_execution_plan_2026-03-18.md`
- `docs/darwin_phase2_compute_normalized_tranche_review_2026-03-18.md`
- `artifacts/darwin/scorecards/compute_normalized_view_v2.json`
- `artifacts/darwin/scorecards/t1_baseline_scorecard.latest.json`
- `artifacts/darwin/reviews/compute_normalized_review_v0.json`
- `artifacts/darwin/memos/compute_normalized_memo_v0.json`

## What changed since the interim tranche review

The interim review said the tranche was still missing:

- stronger comparative memo synthesis using the new scorecard + normalized surface together
- clearer proving-lane narrative for why `lane.harness` and `lane.repo_swe` are semantically clearer now than they were under the Phase-1 companion
- one more review pass confirming the normalized surfaces are decision-useful, not merely better documented

Those items are now satisfied.

## Current proving decision

### `lane.harness`

Current state:

- coherent retained baseline
- no invalid-comparison pressure
- score saturation is now explicitly interpreted rather than silently flattened

Read:

- the new surface is decision-useful because it explains why a flat score lane can still produce meaningful comparative structure

### `lane.repo_swe`

Current state:

- coherent retained baseline
- invalid trials surfaced explicitly
- comparison status and interpretation flags now make invalid-trial handling obvious

Read:

- this is now a strong enough proving case for downstream policy work because the normalized layer distinguishes a valid retained baseline from invalid comparison pressure in a way the old companion did not

## Audit read

### `lane.scheduling`

- audit-only but coherent under the additive normalized layer

### `lane.atp`

- still audit-only and explicitly not the proving center

## Decision

The compute-normalized tranche has now crossed its intended threshold.

It should no longer remain the active Phase-2 proving center.

The correct next move is:

- treat the compute-normalized tranche as sufficiently mature for its current scope
- unlock `breadboard_repo_darwin_phase1_20260313-xrz`
- keep `breadboard_repo_darwin_phase1_20260313-ovi` deferred

## Why `xrz` is now unlocked

The earlier blocker on `xrz` was not missing raw data. It was missing comparative clarity.

That clarity is now in place:

- proving lanes are coherent
- invalid-comparison handling is explicit
- scorecard and memo layers now carry interpretation, not just metrics
- the compute-normalized tranche has become decision-useful rather than merely descriptive

That is enough semantic footing to start the broader transfer-family and lineage-policy tranche.

## Why `ovi` remains deferred

External-safe evidence packaging still should not outrun internal comparative and lineage maturity.

The next correct order is:

1. broader transfer families and lineage policy
2. then external-safe evidence expansion

## Boundary reaffirmed

Even though `xrz` is now unlocked:

- no runtime consumption of tranche-1 compiled artifacts is authorized by this decision alone
- no new BreadBoard kernel-truth primitive is authorized by this decision alone
- transfer/lineage expansion must still be scoped as its own additive-first tranche

## Read

The compute-normalized tranche did what it needed to do.

It improved comparative rigor enough to justify moving on.
