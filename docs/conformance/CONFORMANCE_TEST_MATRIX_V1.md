# Conformance test matrix V1

`CONFORMANCE_TEST_MATRIX_V1.csv` is static CT metadata. It names the CT row, owning area, schema target, fixture source, and gate level. It does not carry mutable status or notes.

Generated CT artifacts are the status source:

- `scripts/run_ct_scenarios.py` writes CT row results. Missing commands are `not_implemented`; successful commands with passing assertions are `pass`; command/assertion failures are `fail`; explicit deferrals are `skipped`.
- P0-blocking and P1-blocking rows with `not_implemented` status fail the CT suite by default. `--fail-on-unimplemented-all` upgrades every missing command to a failing row. `--legacy-planned-ok` is for old dashboards only and is forbidden in CI.
- A `skipped` row needs a deferral allowlist entry with `reason` and `expires_on` or `date`.
- `scripts/sync_conformance_matrix_status.py` reads CT row JSON and writes `artifacts/conformance/CONFORMANCE_TEST_MATRIX_V1.synced.csv` with generated `status` and `notes` columns. Source CSV status never wins over generated rows.
- CT row artifact paths are repo-relative. Absolute host paths belong only under a row `debug` object and are excluded from normal evidence rows.
- Inventory-backed C4 rows use `docs/conformance/e4_lane_inventory.json` as their semantic source: inventory `ct.gate_level` names the evidence class (`C4`), while `scripts/e4_parity/generate_ct_rows.py` renders executable CT scenario `gate_level` values as `<phase>-support` for the accepted E4 lanes.

Round-trip rule: source CSV + CT row JSON must reproduce the synced CSV and sync summaries without hand-editing generated status fields.
