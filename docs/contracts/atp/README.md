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

Current `main` no longer ships the older standalone ATP capability validator referenced in earlier tranches. The closest maintained contract-facing validation entrypoint is the manifest-driven adapter slice runner:

```bash
python scripts/run_bb_atp_adapter_slice_v1.py \
  --manifest <cross-system-manifest.json> \
  --config <breadboard-config.yaml> \
  --system-id breadboard \
  --task-inputs <task-inputs.json> \
  --out-json artifacts/benchmarks/cross_system/breadboard/adapter_slice_run.latest.json
```

This path validates the current manifest/toolchain/budget expectations and emits per-task ATP diagnostics through the current adapter slice flow.

## Minimal retrieval-decomposition loop

For the current merged ATP proving lane, the practical replacement is the formal pack runner:

```bash
python scripts/run_bb_formal_pack_v1.py \
  --manifest <formal-pack-manifest.json> \
  --config <breadboard-config.yaml> \
  --output-dir artifacts/benchmarks/formal_pack/latest
```

This is the better maintained current proving entrypoint for manifest-driven ATP/Hilbert-style formal runs.

## Hilbert comparison and scoreboard workflow

For tranche-level ATP/Hilbert comparison work, start from the current pack builder and then use the scoreboard / rollup builders instead of the removed specialist-fallback matrix runner:

```bash
python scripts/build_hilbert_comparison_packs_v2.py \
  --out-root artifacts/benchmarks/hilbert_comparison_packs_v2
```

Then build the downstream comparison surfaces:

```bash
python scripts/build_atp_hilbert_scoreboard_v1.py \
  --out-json artifacts/benchmarks/hilbert_comparison_packs_v2/scoreboard_v1.json \
  --out-md artifacts/benchmarks/hilbert_comparison_packs_v2/scoreboard_v1.md
```

See also:

- `scripts/build_hilbert_comparison_packs_v2.py`
- `scripts/build_hilbert_bb_comparison_bundle_v1.py`
- `scripts/build_atp_hilbert_rollup_v1.py`
- `scripts/build_atp_hilbert_canonical_baselines_v1.py`
- `scripts/build_atp_hilbert_arm_audit_v1.py`
- `scripts/build_atp_hilbert_no_repair_slice_v1.py`
- `scripts/run_bb_atp_adapter_slice_v1.py`
- `scripts/run_bb_formal_pack_v1.py`

## Current maintained ATP entrypoints

For the current merged ATP surface, the maintained flow is:

1. generate or refresh Hilbert pack inputs:
   - `scripts/build_hilbert_comparison_packs_v2.py`
2. run bounded proving or adapter execution:
   - `scripts/run_bb_formal_pack_v1.py`
   - `scripts/run_bb_atp_adapter_slice_v1.py`
3. build tranche-level comparison and audit outputs:
   - `scripts/build_hilbert_bb_comparison_bundle_v1.py`
   - `scripts/build_atp_hilbert_scoreboard_v1.py`
   - `scripts/build_atp_hilbert_rollup_v1.py`
   - `scripts/build_atp_hilbert_arm_audit_v1.py`
