# Projection Surface Expansion Prep (V1)

This prep artifact standardizes how future projection consumers (for example:
desktop app, IDE-web hybrid, additional app shells) are added to parity
gating without weakening existing guarantees.

## Current state

1. `scripts/check_projection_surface_parity_bundle.py` supports:
   - default built-in surface set (Wave A current behavior)
   - optional `--surface-manifest` override for explicit required surfaces.
2. Default active surfaces:
   - `sdk`
   - `opentui`
   - `webapp`
   - `tui`
   - `vscode`
3. Baseline manifest:
   - `docs/conformance/projection_surface_manifest_v1.json`

## Expansion protocol (new surface)

1. Implement surface-level parity checker that emits `cases_total`,
   `cases_failed`, and `ok` in a deterministic JSON report.
2. Add canonical fixture parity cases under:
   - `tests/fixtures/conformance_v1/projection_semantics/`
3. Add checker unit tests.
4. Add report path to `projection_surface_manifest_v1.json`.
5. Run aggregate bundle with manifest:
   - `python scripts/check_projection_surface_parity_bundle.py --artifact-dir artifacts/conformance --surface-manifest docs/conformance/projection_surface_manifest_v1.json --json-out artifacts/conformance/projection_surface_parity_bundle_v1.json`
6. Add/extend CI path triggers and gate tests for the new checker/report.

## Safety requirements

1. Never remove existing surfaces from manifest unless formally deprecated.
2. New surfaces must fail the aggregate bundle when report is missing.
3. No reduction of current Wave A surface coverage while expanding.
