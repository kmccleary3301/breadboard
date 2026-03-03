# Gap Closure Criteria (V1)

Purpose:

1. Define canonical status vocabulary for `G-*` closure accounting.
2. Make progress scoring deterministic and script-checkable.
3. Keep conformance matrix and gap-map evidence coherent.

## Status vocabulary

`docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.csv`:

1. `open`: no sufficient direct CT/checker proof.
2. `partial`: direct proof exists but closure delta remains.
3. `covered`: direct CT/checker proof exists for the tracked scope.

## Completion weights (for progress scoring)

1. `open` => `0.00`
2. `partial` => `0.60`
3. `covered` => `1.00`

## Required fields

1. `gap_id` must be unique and match `G-###`.
2. `current_status` must be one of `open|partial|covered`.
3. `remaining_closure_delta` must be non-empty for `open|partial`.
4. `covered` rows must reference at least one `CT-*` id in `ct_coverage`.

## Gate scripts

1. `scripts/check_gap_status_vocabulary.py`
2. `scripts/check_gap_closure_consistency.py`
3. `scripts/generate_gap_progress_report.py`
4. `scripts/check_gap_closure_rubric.py`
5. `scripts/check_live_evidence_ct_references.py`
6. `scripts/build_live_evidence_pack.py`
7. `scripts/check_live_evidence_freshness.py`

## One-command refresh

Use:

1. `scripts/refresh_conformance_ledgers.sh artifacts/conformance`

This runs the Wave-A bundle, refreshes synced matrix docs outputs, and regenerates unresolved competitor delta views when available.
