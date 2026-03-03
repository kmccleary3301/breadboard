# ATP Contract Pack (V1)

This directory defines ATP extension-facing contract schemas kept outside the
kernel danger zone.

## Schemas

- `schemas/retrieval_adapter_request_v1.schema.json`
- `schemas/retrieval_adapter_response_v1.schema.json`
- `schemas/specialist_solver_request_v1.schema.json`
- `schemas/specialist_solver_response_v1.schema.json`
- `schemas/specialist_solver_trace_v1.schema.json`
- `schemas/search_loop_decomposition_node_event_v1.schema.json`
- `schemas/search_loop_decomposition_trace_v1.schema.json`
- `schemas/atp_proof_plan_ir_v1.schema.json`
- `schemas/atp_diagnostic_record_v1.schema.json`
- `schemas/atp_repair_policy_decision_v1.schema.json`
- `schemas/atp_retrieval_snapshot_v1.schema.json`
- `schemas/atp_runtime_instrumentation_digest_v1.schema.json`

Diagnostics normalization doc:

- `ATP_DIAGNOSTICS_NORMALIZATION_V1.md`

Repair policy logging doc:

- `ATP_REPAIR_POLICY_LOGGING_V1.md`

## Validation

Use the fixture validator to confirm schema compatibility:

```bash
python scripts/validate_atp_capability_contracts.py \
  --fixture tests/fixtures/atp_capabilities/retrieval_solver_replay_fixture_v1.json \
  --schema-dir docs/contracts/atp/schemas \
  --json-out artifacts/atp_capabilities/contract_validation_report.latest.json
```

## Minimal retrieval-decomposition loop

```bash
python scripts/run_atp_retrieval_decomposition_loop_v1.py \
  --fixture tests/fixtures/atp_capabilities/retrieval_solver_replay_fixture_v1.json \
  --schema-dir docs/contracts/atp/schemas \
  --artifacts-dir artifacts/atp_retrieval_decomposition_v1 \
  --out artifacts/atp_retrieval_decomposition_v1/atp_retrieval_decomposition_report.latest.json
```

## Specialist fallback trace matrix

```bash
python scripts/run_atp_specialist_solver_fallback_matrix_v1.py \
  --schema-dir docs/contracts/atp/schemas \
  --artifacts-dir artifacts/atp_specialist_solver_fallback_matrix_v1 \
  --out artifacts/atp_specialist_solver_fallback_matrix_v1/solver_fallback_matrix_report.latest.json
```
