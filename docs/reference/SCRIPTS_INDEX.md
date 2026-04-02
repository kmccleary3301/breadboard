# Scripts Index

This page is the stable reference for the current `scripts/` surface.

It does two jobs:

- explain what script categories mean
- document the canonical script taxonomy that current low-risk moves are already using

The machine-readable inventory lives at:

- [`scripts/_inventory/python_scripts_inventory_20260401.csv`](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_integration_main_20260326/scripts/_inventory/python_scripts_inventory_20260401.csv)

Summary counts live at:

- [`scripts/_inventory/python_scripts_inventory_summary_20260401.md`](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_integration_main_20260326/scripts/_inventory/python_scripts_inventory_summary_20260401.md)

## Current scope

Current Python scripts inventoried: `260`

Primary category counts:

- `research`: `229`
- `migration`: `10`
- `release`: `8`
- `ops`: `5`
- `dev`: `5`
- `archive`: `3`

## Category meanings

- `dev`: local setup, local helper, and devx scripts
- `research`: study, benchmark, replay, evaluation, and experiment scripts
- `release`: scripts that generate or bless durable release-facing artifacts
- `ops`: maintenance, health, doctor, and operational helper scripts
- `migration`: conversion and import/export bridge scripts
- `archive`: obvious stubs or short-lived historical scripts

## Canonical categorized families now live

The taxonomy is no longer just planned. These canonical families now exist in
the live repo:

- `scripts/dev/`
- `scripts/ops/`
- `scripts/migration/`
- `scripts/release/`
- `scripts/archive/`
- `scripts/research/parity/`

## Stable vs internal expectation

Treat these as the more stable script families:

- `dev`
- `release`
- selected `ops`

Treat these as primarily internal:

- most `research`
- most `migration`
- `archive`

## Canonical taxonomy

```text
scripts/
├── dev/
├── research/
│   ├── atp/
│   ├── darwin/
│   ├── parity/
│   ├── longrun_rlm/
│   ├── provider/
│   ├── rendering/
│   └── repo_hygiene/
├── release/
├── ops/
├── migration/
└── archive/
```

## Canonical stable entrypoints

Prefer the canonical categorized paths when writing new docs, scripts, or
examples:

- `scripts/ops/fixtures_doctor.py`
- `scripts/ops/cli_session_health.py`
- `scripts/ops/preflight_workspace_safety.py`
- `scripts/ops/export_provider_metrics.py`
- `scripts/migration/import_ir_to_events_jsonl.py`
- `scripts/migration/compat_dump_request_bodies.py`
- `scripts/migration/recover_missing_files_from_codex_outputs.py`
- `scripts/release/export_cli_bridge_contracts.py`
- `scripts/release/validate_kernel_contract_fixtures.py`
- `scripts/release/bless_golden.py`
- `scripts/research/parity/audit_e4_target_drift.py`
- `scripts/research/parity/check_e4_snapshot_coverage.py`

The old top-level script paths still exist only as compatibility wrappers while
the migration window is open.

## Low-risk moved slices

- everything already under `scripts/dev/`
- conversion and migration scripts such as `convert_*`, `migrate_*`, and `scripts/migration/import_ir_to_events_jsonl.py`
- narrow ops scripts such as `scripts/ops/fixtures_doctor.py`, `scripts/ops/cli_session_health.py`, `scripts/ops/preflight_workspace_safety.py`, and `scripts/ops/export_provider_metrics.py`
- additional low-risk ops metrics scripts such as `scripts/ops/guardrail_metrics.py`
- obvious archive candidates such as:
  - `scripts/archive/phase11_benchmark_runner_stub.py`
  - `scripts/archive/phase11_export_trajectory_stub.py`
  - `scripts/archive/phase11_paired_eval_stub.py`
- stable release-facing generators and validators such as:
  - `scripts/release/export_cli_bridge_contracts.py`
  - `scripts/release/validate_kernel_contract_fixtures.py`
  - `scripts/release/bless_golden.py`
- parity-oriented research helpers such as:
  - `scripts/research/parity/audit_e4_target_drift.py`
  - `scripts/research/parity/check_e4_snapshot_coverage.py`
