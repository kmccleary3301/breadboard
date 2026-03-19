# DARWIN Phase-2 Transfer and Lineage Tranche Status

Date: 2026-03-19
Status: tranche review complete
Primary issue: `breadboard_repo_darwin_phase1_20260313-xrz`

## What landed

- transfer-family policy:
  - `docs/contracts/darwin/DARWIN_TRANSFER_FAMILY_POLICY_V0.md`
- lineage policy:
  - `docs/contracts/darwin/DARWIN_LINEAGE_POLICY_V0.md`
- derived transfer-family view:
  - `artifacts/darwin/reviews/transfer_family_view_v0.json`
- derived lineage review:
  - `artifacts/darwin/reviews/lineage_review_v0.json`
- transfer/lineage proving review:
  - `artifacts/darwin/reviews/transfer_lineage_proving_review_v0.json`

## Current read

- transfer work is still bounded and additive-first
- repo_swe remains the key invalid-comparison-sensitive proving lane
- scheduling remains the key lineage proving lane
- harness/research remain bounded source/target roles
- ATP remains audit-only
- proving review now exists for repo_swe, scheduling, harness, research, and ATP
- EvolutionLedger semantics now distinguish retained-baseline decisions from promotions and deprecations
- tranche review now recommends external-safe evidence packaging as the next scoped tranche

## Validation

- `pytest -q tests/test_build_darwin_transfer_family_view_v0.py tests/test_build_darwin_lineage_review_v0.py`
- `pytest -q tests/test_build_darwin_transfer_lineage_proving_review_v0.py`
- `python scripts/build_darwin_transfer_family_view_v0.py --json`
- `python scripts/build_darwin_lineage_review_v0.py --json`
- `python scripts/build_darwin_transfer_lineage_proving_review_v0.py --json`

## Boundary

- runtime truth unchanged
- no new kernel-truth primitive
- no runtime consumption authorized by these derived views
- runtime truth remains unchanged
- external-safe evidence is now the recommended next tranche rather than an active part of this one
