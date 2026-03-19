# DARWIN Current-Program Merged-State Verification

Date: 2026-03-19
Status: completed on `main`

## Verification target

Branch verified: `main`

## Validation commands

- `pytest -q tests/test_build_darwin_external_safe_claim_subset_v0.py tests/test_build_darwin_external_safe_invalidity_summary_v0.py tests/test_build_darwin_external_safe_reviewer_summary_v0.py tests/test_build_darwin_external_safe_memo_v0.py tests/test_build_darwin_external_safe_packet_v0.py`
- `python scripts/build_darwin_external_safe_claim_subset_v0.py --json`
- `python scripts/build_darwin_external_safe_invalidity_summary_v0.py --json`
- `python scripts/build_darwin_external_safe_reviewer_summary_v0.py --json`
- `python scripts/build_darwin_external_safe_memo_v0.py --json`
- `python scripts/build_darwin_external_safe_packet_v0.py --json`

## Results

- `5` external-safe DARWIN tests passed on merged `main`.
- external-safe claim subset emitted `5` included claims.
- external-safe invalidity summary emitted `4` caveats.
- external-safe reviewer summary emitted `5` lane rows.
- external-safe evidence memo and packet both emitted successfully.
- contract-pack validation remained `ok: true`.
