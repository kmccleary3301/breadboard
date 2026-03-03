# Replay Evidence Baselines (Non-Fixture)

This folder stores non-fixture replay determinism evidence snapshots consumed by
Wave A release gating.

Purpose:

1. Ensure replay gate coverage includes artifacts outside `tests/fixtures/`.
2. Keep a stable, versioned baseline for deterministic quality checks.
3. Preserve minimal but non-trivial metadata (`turn_count`, `scenario_id`,
   `provider`, `model`) for auditing.

Notes:

1. Files here are expected to use `schema_version: replay_determinism_report_v1`.
2. Each file should represent a meaningful run (`turn_count >= 15`).
3. Wave-A release gate currently requires at least `6` non-fixture replay reports
   (and at least `8` total replay reports including fixture lanes).
4. Files are consumed by:
   - `scripts/run_wave_a_conformance_bundle.sh`
   - `docs/conformance/ct_scenarios_v1.json` (`CT-REL-002`)
