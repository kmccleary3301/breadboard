# Adding a CT Row Correctly (Wave A)

This guide is the required checklist for adding a new `CT-*` row to Wave A conformance.

## 1) Add row to matrix

1. Edit `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`.
2. Add unique `test_id` and correct `gate_level`.
3. Keep ownership and notes specific to the row.

## 2) Add executable scenario entry

1. Edit `docs/conformance/ct_scenarios_v1.json`.
2. Add one scenario object with:
   1. `test_id`
   2. `description`
   3. `command` (deterministic command shape)
   4. `timeout_seconds`
   5. `assertions.json_files` with deterministic path checks
   6. `gate_level`
3. Ensure artifact paths use deterministic naming:
   1. `artifacts/conformance/node_gate/ct_<normalized_test_id>.json`
   2. `artifacts/conformance/junit/ct_<normalized_test_id>.xml` (when using node-gate)

## 3) Pick approved command shape

Non-reliability rows must use one of:

1. `python scripts/run_pytest_node_gate.py ...`
2. `python scripts/check_projection_semantics.py ...`
3. `python scripts/check_tool_transition_semantics.py ...`

If you add a new command shape, update:

1. `tests/test_ct_scenarios_manifest_hardening.py`

## 4) Add tests/fixtures

1. Add or update row-level tests/fixtures for semantic behavior.
2. If adding new schema artifacts, add:
   1. schema file in `docs/conformance/schemas/`
   2. valid fixture in `tests/fixtures/conformance_v1/fixtures/valid/`
   3. invalid fixture in `tests/fixtures/conformance_v1/fixtures/invalid/`

## 5) Required validation commands

Run:

```bash
pytest -q \
  tests/test_ct_scenarios_manifest_hardening.py \
  tests/test_run_ct_scenarios.py \
  tests/test_run_wave_a_conformance_bundle.py
```

Then run:

```bash
scripts/run_wave_a_conformance_bundle.sh artifacts/conformance
```

Expected:

1. `ct-scenarios status=pass`
2. `mapped=all rows`
3. `validate-conformance-artifacts pass`

## 6) CI integration

If you add new script/test files, update:

1. `.github/workflows/conformance-wave-a-gate.yml`

to include:

1. path triggers
2. unit test invocation.
