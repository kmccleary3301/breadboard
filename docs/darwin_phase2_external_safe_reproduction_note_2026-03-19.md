# DARWIN External-Safe Reproduction Note

Date: 2026-03-19
Status: bounded reproduction note for the external-safe evidence tranche
Primary issue: `breadboard_repo_darwin_phase1_20260313-ovi`

## Scope

This note reproduces the current scoped DARWIN evidence program only.

It does not attempt to replay all historical tranche-building steps.

## Source-of-truth build sequence

1. `python scripts/run_darwin_phase1_orchestration_v1.py --json`
2. `python scripts/run_darwin_phase1_replay_audit_v1.py --json`
3. `python scripts/build_darwin_compute_normalized_view_v2.py --json`
4. `python scripts/run_darwin_t1_baseline_scorecard_v1.py --json --include-search`
5. `python scripts/build_darwin_compute_normalized_review_v0.py --json`
6. `python scripts/build_darwin_compute_normalized_memo_v0.py --json`
7. `python scripts/build_darwin_evolution_ledger_v0.py --json`
8. `python scripts/build_darwin_transfer_family_view_v0.py --json`
9. `python scripts/build_darwin_lineage_review_v0.py --json`
10. `python scripts/build_darwin_transfer_lineage_proving_review_v0.py --json`
11. `python scripts/build_darwin_external_safe_claim_subset_v0.py --json`
12. `python scripts/build_darwin_external_safe_invalidity_summary_v0.py --json`
13. `python scripts/build_darwin_external_safe_reviewer_summary_v0.py --json`
14. `python scripts/build_darwin_external_safe_memo_v0.py --json`
15. `python scripts/build_darwin_external_safe_packet_v0.py --json`

## Expected outputs

- `artifacts/darwin/scorecards/t1_baseline_scorecard.latest.json`
- `artifacts/darwin/scorecards/compute_normalized_view_v2.json`
- `artifacts/darwin/reviews/compute_normalized_review_v0.json`
- `artifacts/darwin/memos/compute_normalized_memo_v0.json`
- `artifacts/darwin/search/evolution_ledger_v0.json`
- `artifacts/darwin/reviews/transfer_family_view_v0.json`
- `artifacts/darwin/reviews/lineage_review_v0.json`
- `artifacts/darwin/reviews/transfer_lineage_proving_review_v0.json`
- `artifacts/darwin/claims/external_safe_claim_subset_v0.json`
- `artifacts/darwin/reviews/external_safe_invalidity_summary_v0.json`
- `artifacts/darwin/reviews/external_safe_reviewer_summary_v0.json`
- `artifacts/darwin/memos/external_safe_evidence_memo_v0.json`
- `artifacts/darwin/evidence/external_safe_evidence_packet_v0.json`

## Derived vs source-of-truth

Source-of-truth runtime artifacts remain the existing live-baseline, search, replay, and scorecard outputs.

The external-safe layer is derived-only and should not be treated as runtime truth.
