# DARWIN Current-Program Merged-State Verification

Date: 2026-03-19
Status: pending branch landing to `main`

## Intended verification target

Branch to verify once landed: `main`

## Intended validation commands

- `pytest -q tests/test_build_darwin_external_safe_claim_subset_v0.py tests/test_build_darwin_external_safe_invalidity_summary_v0.py tests/test_build_darwin_external_safe_reviewer_summary_v0.py tests/test_build_darwin_external_safe_memo_v0.py tests/test_build_darwin_external_safe_packet_v0.py`
- `python scripts/build_darwin_external_safe_claim_subset_v0.py --json`
- `python scripts/build_darwin_external_safe_invalidity_summary_v0.py --json`
- `python scripts/build_darwin_external_safe_reviewer_summary_v0.py --json`
- `python scripts/build_darwin_external_safe_memo_v0.py --json`
- `python scripts/build_darwin_external_safe_packet_v0.py --json`

## Current read

This verification document is intentionally not checked complete on the feature branch.

It exists so the merged-state verification step is explicit and reproducible once the branch is landed to `main`.
