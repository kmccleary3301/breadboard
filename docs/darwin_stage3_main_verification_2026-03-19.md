# DARWIN Stage-3 Main Verification

Date: 2026-03-19
Status: complete on `main`

This note records merged-state verification on `main` for the final Stage-3 closeout tranche.

## Verification

- `pytest -q tests/test_darwin_stage3_inference.py tests/test_run_darwin_stage3_bounded_inference_campaign_v0.py tests/test_build_darwin_stage3_reports_v0.py tests/test_build_darwin_stage3_component_transfer_v0.py tests/test_build_darwin_stage3_precloseout_v0.py` → `9 passed`
- `python scripts/build_darwin_stage3_component_registry_v0.py --json` → `row_count=5`
- `python scripts/build_darwin_stage3_promoted_family_artifact_v0.py --json` → passed
- `python scripts/build_darwin_stage3_second_family_decision_v0.py --json` → passed
- `python scripts/build_darwin_stage3_component_scorecard_v0.py --json` → `row_count=5`
- `python scripts/build_darwin_stage3_component_memo_v0.py --json` → passed
- `python scripts/build_darwin_stage3_consolidated_verification_bundle_v0.py --json` → `registry_rows=5`

## Result

Merged-state verification passed on `main`. The rebuilt canonical Stage-3 artifacts match the closeout docs and the canonical artifact freeze.
