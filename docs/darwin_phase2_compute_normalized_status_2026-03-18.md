# DARWIN Phase-2 Compute-Normalized Tranche Status

Date: 2026-03-18
Status: first implementation slice landed
Primary issue: `breadboard_repo_darwin_phase1_20260313-8xc`

## What landed

- stronger normalization policy doc:
  - `docs/contracts/darwin/DARWIN_COMPUTE_NORMALIZED_SCORECARD_V2.md`
- explicit cost-accounting classification:
  - `docs/contracts/darwin/DARWIN_COST_ACCOUNTING_CLASSIFICATION_V0.md`
- upgraded additive artifact:
  - `artifacts/darwin/scorecards/compute_normalized_view_v2.json`
- dossier support now prefers the v2 normalized view when present
- comparative scorecard now consumes the v2 normalized semantics when present
- proving review artifact now exists:
  - `artifacts/darwin/reviews/compute_normalized_review_v0.json`
  - `artifacts/darwin/reviews/compute_normalized_review_v0.md`
- comparative memo artifact now exists:
  - `artifacts/darwin/memos/compute_normalized_memo_v0.json`
  - `artifacts/darwin/memos/compute_normalized_memo_v0.md`
- tranche review note now exists:
  - `docs/darwin_phase2_compute_normalized_tranche_review_2026-03-18.md`
- tranche decision note now exists:
  - `docs/darwin_phase2_compute_normalized_tranche_decision_2026-03-18.md`

## What changed semantically

Compared with the Phase-1 companion view, the v2 normalized surface now makes the following explicit:

- runtime-normalized score is evaluator wall-clock normalized only
- local cost accounting is classified as exact, estimated, or unavailable
- zero-cost local evaluator runs do not produce fake score-per-USD values
- external billing remains unavailable
- lane rows carry comparison status and interpretation flags

## Current proving read

Primary proving lanes for the tranche remain:

- `lane.harness`
- `lane.repo_swe`

The new normalized view is also emitted across all current active lanes as a derived comparative surface, but this does not change the proving-lane boundary.

## Validation

- `pytest -q tests/test_build_darwin_compute_normalized_view_v2.py tests/test_build_darwin_comparative_dossier_v1.py`
- `pytest -q tests/test_run_darwin_t1_baseline_scorecard_v1.py`
- `pytest -q tests/test_build_darwin_compute_normalized_review_v0.py`
- `pytest -q tests/test_build_darwin_compute_normalized_memo_v0.py`
- `python scripts/build_darwin_compute_normalized_view_v2.py --json`
- `python scripts/build_darwin_compute_normalized_review_v0.py --json`
- `python scripts/build_darwin_compute_normalized_memo_v0.py --json`

## Boundary

- runtime truth unchanged
- no new kernel-truth primitive
- no runtime consumption of tranche-1 compiled artifacts
- no broader transfer-family expansion in this slice

## Current tranche decision read

- compute-normalized tranche is now strong enough for its current scope
- broader transfer families / lineage policy can now be unlocked as the next scoped tranche
- external-safe evidence remains deferred
