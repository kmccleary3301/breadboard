# EvoLake Campaign Manifest v1 (Draft)

Date: 2026-02-24

## Purpose

Standardize campaign metadata and checkpoint integrity for replayable vs exploratory EvoLake execution.

## Campaign Manifest

Schema:

- `docs/contracts/evolake/schemas/evolake_campaign_manifest_v1.schema.json`

Required fields:

- `campaign_id`
- `mode` (`replay | explore`)
- `mode_policy` (`replay_allowed`, `explore_allowed`, `default_mode`)
- `rng_seed`
- `budget.max_generations`
- `budget.max_time_s`
- `objective.primary_metric`
- `objective.secondary_metrics`
- `objective.selection_rule`
- `schema_refs`

## Checkpoint Integrity Envelope

Schema:

- `docs/contracts/evolake/schemas/evolake_checkpoint_envelope_v1.schema.json`

Required integrity fields:

- `mode`
- `rng_seed`
- `budget`
- `schema_refs`
- `event_log_sha256`

## Runtime Integration

Emitter:

- `scripts/run_evolake_minimal_campaign_v1.py`

Output fields in report:

- `campaign_manifest_path`
- `campaign_manifest_validation_ok`
- `checkpoint_validation_ok`
- `mode`
- `rng_seed`
- `best_candidate_quarantined`

Checkpoint verifier command:

- `python scripts/check_evolake_checkpoint_integrity_v1.py --checkpoint <path> --schema-dir docs/contracts/evolake/schemas`

This keeps replay-mode vs explore-mode explicit while preserving extension-only scope.
