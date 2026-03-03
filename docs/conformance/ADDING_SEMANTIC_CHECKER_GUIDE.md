# Adding a Semantic Checker Correctly

Semantic checkers raise evidence depth beyond simple node-level pytest pass/fail.

Current semantic checkers:

1. `scripts/check_projection_semantics.py`
2. `scripts/check_tool_transition_semantics.py`
3. `scripts/check_reliability_lanes.py`

## 1) Contract

Each checker must:

1. accept explicit input artifact paths via CLI args.
2. emit a deterministic JSON report via `--json-out`.
3. include `schema_version`.
4. include `ok` boolean.
5. return `0` on pass, non-zero on policy/semantic failure.

## 2) Fixture discipline

Add deterministic fixtures under:

1. `tests/fixtures/conformance_v1/<family>/`

Minimum fixture set:

1. pass fixture
2. fail fixture

## 3) Unit tests

Add a checker unit test file:

1. `tests/test_check_<checker>.py`

Must cover:

1. pass path
2. fail path
3. output schema keys.

## 4) CT manifest wiring

Wire checker into `docs/conformance/ct_scenarios_v1.json`:

1. row command points to checker script.
2. deterministic `--json-out` path.
3. row `assertions.json_files` verifies:
   1. `ok`
   2. key semantic metrics (counts/rates/violations).

## 5) Artifact validator integration

If checker output is bundle-critical:

1. add report path to required artifacts in `scripts/validate_conformance_artifacts.py`.
2. add key-level checks there.

## 6) CI gate integration

Update `.github/workflows/conformance-wave-a-gate.yml`:

1. path triggers for new checker script and tests.
2. include new test in unit step.

## 7) Acceptance runbook

Run:

```bash
pytest -q tests/test_check_<checker>.py tests/test_run_wave_a_conformance_bundle.py
scripts/run_wave_a_conformance_bundle.sh artifacts/conformance
python scripts/validate_conformance_artifacts.py --artifact-dir artifacts/conformance
```

Only merge when all commands pass.
