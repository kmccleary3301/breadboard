# Benchmark Contract Pack (P3)

This directory holds benchmark-facing contract scaffolds used by ATP/EvoLake capability lanes.

## Schemas

- `schemas/benchmark_run_manifest_v1.schema.json`
- `schemas/cross_system_run_manifest_v1.schema.json`
- `schemas/evidence_bundle_manifest_v2.schema.json`
- `schemas/claim_ledger_v1.schema.json`
- `schemas/spend_attribution_record_v1.schema.json`
- `schemas/p2_kpi_snapshot_v1.schema.json`
- `schemas/normalized_prover_result_v1.schema.json`

## Manifest Hash + Snapshot Pinning

`benchmark_run_manifest_v1` supports deterministic replay metadata:

- `manifest_hash_sha256`: sha256 over canonical manifest JSON excluding `manifest_hash_sha256`.
- `fixture_relpath`: fixture location used to generate the run.
- `fixture_sha256`: digest of fixture contents.
- `dataset_snapshot_sha256`: digest of the dataset snapshot identity for the run.

Validation policy in `scripts/validate_benchmark_reports_v1.py`:

- All benchmark manifests must carry a valid `manifest_hash_sha256`.
- `*_subset_v1` benchmark runs must carry fixture and dataset snapshot digests.

This keeps benchmark claims auditable and prevents silent fixture drift between runs.

## Claim-Evidence Linkage

`scripts/validate_benchmark_reports_v1.py` supports claim linkage enforcement:

- `--require-claim-linkage`: each report must provide either:
  - `claim_id` (and optionally resolve against `--claim-ledger`), or
  - `no_claim_mode = {"enabled": true, "reason": ...}` for exploratory runs.
- `--claim-ledger <path>`: validates that `claim_id` exists in ledger claims.

This implements the “claim or explicit no-claim” contract needed for Tiered evidence governance.

## Cross-System Comparison Contracts (P4)

Two additional schemas support Aristotle/BB comparison protocol scaffolding:

- `cross_system_run_manifest_v1.schema.json`: pre-registered comparison manifest with
  frozen benchmark slice/task IDs, pinned toolchain metadata, budget class, and
  system roster.
- `normalized_prover_result_v1.schema.json`: normalized per-task result rows for
  black-box and white-box systems (`task_id`, `prover_system`, `status`,
  `verification_log_digest`, and budget/toolchain keys).

Reference scripts:

- `scripts/validate_cross_system_run_v1.py`
- `scripts/build_pilot_comparison_report_v1.py`
- `scripts/run_aristotle_adapter_slice_v1.py`
- `scripts/run_bb_atp_adapter_slice_v1.py`
- `scripts/run_cross_system_pilot_bundle_v1.py`
- `scripts/build_cross_system_frozen_slices_v1.py`

`scripts/run_bb_atp_adapter_slice_v1.py` is the real BreadBoard execution path for
Hilbert comparison packs. It creates a per-task workspace, launches an actual
BreadBoard session against the supplied config/model, verifies the resulting Lean
file against Kimina, and emits `normalized_prover_result_v1` rows plus raw
diagnostic JSON.

Example invocation:

```bash
cd breadboard_repo
python scripts/run_bb_atp_adapter_slice_v1.py \
  --manifest artifacts/benchmarks/hilbert_comparison_packs_v1/pack_a_seedproof_sanity_minif2f_v1/cross_system_manifest.json \
  --task-inputs artifacts/benchmarks/hilbert_comparison_packs_v1/pack_a_seedproof_sanity_minif2f_v1/bb_task_inputs.json \
  --config agent_configs/atp_bb_aristotle_match_codexmini_v1.yaml \
  --model openrouter/openai/gpt-5.4 \
  --base-url http://127.0.0.1:9599 \
  --start-engine \
  --engine-port 9599 \
  --verifier-url http://127.0.0.1:18001/verify \
  --out artifacts/benchmarks/hilbert_comparison_packs_v1/pack_a_seedproof_sanity_minif2f_v1/bb_atp_normalized_results_v1.jsonl \
  --summary-out artifacts/benchmarks/hilbert_comparison_packs_v1/pack_a_seedproof_sanity_minif2f_v1/bb_atp_slice_summary_v1.json
```

Adapter input fixture example:

- `tests/fixtures/benchmarks/cross_system_task_inputs_demo_v1.json`
