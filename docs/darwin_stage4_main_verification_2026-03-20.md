# DARWIN Stage-4 Main Verification

Date: 2026-03-20
Status: complete on `main`

This note records merged-state verification on `main` for the final Stage-4 closeout tranche.

## Verification

- `pytest -q tests/test_darwin_stage4.py tests/test_run_darwin_stage4_live_economics_pilot_v0.py tests/test_run_darwin_stage4_systems_live_pilot_v0.py tests/test_run_darwin_stage4_deep_live_search_v0.py tests/test_build_darwin_stage4_ev_reports_v0.py tests/test_build_darwin_stage4_systems_ev_bundle_v0.py tests/test_build_darwin_stage4_deep_live_reports_v0.py tests/test_run_darwin_stage4_replay_checks_v0.py tests/test_build_darwin_stage4_live_readiness_v0.py tests/test_build_darwin_stage4_matched_budget_view_v1.py tests/test_build_darwin_stage4_family_program_v0.py tests/test_build_darwin_stage4_precloseout_v0.py tests/test_darwin_contract_pack_v0.py` → `56 passed`
- `python scripts/run_darwin_stage4_deep_live_search_v0.py --json` → `round_count=4`, `run_count=37`
- `python scripts/build_darwin_stage4_deep_live_reports_v0.py --json` → `strongest_family_count=5`
- `python scripts/run_darwin_stage4_replay_checks_v0.py --json` → `row_count=2`
- `python scripts/build_darwin_stage4_family_candidates_v0.py --json` → `row_count=4`
- `python scripts/build_darwin_stage4_family_promotion_report_v0.py --json` → `row_count=4`
- `python scripts/build_darwin_stage4_second_family_decision_v0.py --json` → `decision=two_promoted_families`
- `python scripts/build_darwin_stage4_family_registry_v0.py --json` → `row_count=4`
- `python scripts/build_darwin_stage4_bounded_transfer_outcomes_v0.py --json` → `row_count=3`
- `python scripts/build_darwin_stage4_failed_transfer_taxonomy_v0.py --json` → `row_count=2`
- `python scripts/build_darwin_stage4_family_scorecard_v0.py --json` → `row_count=4`
- `python scripts/build_darwin_stage4_family_memo_v0.py --json` → passed
- `python scripts/build_darwin_stage4_family_verification_bundle_v0.py --json` → `promoted_family_count=2`
- `python scripts/run_darwin_stage4_family_replay_audit_v0.py --json` → `row_count=2`
- `python scripts/build_darwin_stage4_canonical_artifact_index_v0.py --json` → `row_count=14`
- `python scripts/build_darwin_stage4_comparative_bundle_v0.py --json` → `ref_count=12`
- `python scripts/validate_darwin_contract_pack_v0.py --json` → `ok: true`

## Result

Merged-state verification passed on `main`. The rebuilt canonical Stage-4 artifacts match the closeout docs and the canonical artifact freeze.
