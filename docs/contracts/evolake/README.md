# EvoLake Contract Pack (V1)

This directory defines EvoLake extension-facing manifest/checkpoint contracts.

## Schemas

- `schemas/evolake_campaign_manifest_v1.schema.json`
- `schemas/evolake_checkpoint_envelope_v1.schema.json`
- `schemas/evaluator_integrity_report_v1.schema.json`

## Current emitter

- `scripts/run_evolake_minimal_campaign_v1.py`
  - emits `campaign_manifest.json`
  - writes checkpoint with integrity envelope fields (`mode`, `rng_seed`, `budget`, `schema_refs`, `event_log_sha256`)
- `scripts/check_evolake_checkpoint_integrity_v1.py`
  - validates checkpoint envelope schema + `event_log_sha256`
- `scripts/run_evolake_evaluator_integrity_harness_v1.py`
  - emits evaluator integrity + quarantine report
