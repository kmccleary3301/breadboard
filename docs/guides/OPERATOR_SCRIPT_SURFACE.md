# Operator Script Surface

BreadBoard now has a real script taxonomy.

This guide explains how to use it without having to infer the intended family
from file names alone.

If you only need the raw categorized list, use
[../reference/SCRIPTS_INDEX.md](../reference/SCRIPTS_INDEX.md).

If you want the shortest practical answer to "which script family should I use
for this task?", start here.

## The short version

Use these rules first:

- `scripts/dev/` for first-run setup, local doctor checks, and devx helpers
- `scripts/ops/` for operator and maintainer health checks
- `scripts/release/` for contract, fixture, and release-facing generation or
  blessing
- `scripts/migration/` for conversion, import, and bridge helpers
- `scripts/research/parity/` for parity and target-drift research helpers
- `scripts/archive/` only when you are intentionally touching preserved stubs

If you are reaching for a top-level legacy wrapper path, assume there is a
better canonical categorized path and check
[../reference/SCRIPTS_INDEX.md](../reference/SCRIPTS_INDEX.md) first.

## Which family should I use?

### You are trying to get the repo healthy locally

Start with:

- `python scripts/dev/first_time_doctor.py --strict`
- `python scripts/ops/fixtures_doctor.py --help`
- `python scripts/ops/cli_session_health.py --help`

### You are validating workspace or execution safety

Start with:

- `python scripts/ops/preflight_workspace_safety.py --help`
- [../reference/SAFE_MODE_EXECUTION_POLICY.md](../reference/SAFE_MODE_EXECUTION_POLICY.md)

### You are generating or validating durable release-facing artifacts

Start with:

- `python scripts/release/export_cli_bridge_contracts.py --help`
- `python scripts/release/validate_kernel_contract_fixtures.py --help`
- `python scripts/release/bless_golden.py --help`

These are the canonical release-facing entrypoints. Prefer them over the old
top-level wrapper paths.

### You are importing, converting, or recovering artifacts

Start with:

- `python scripts/migration/import_ir_to_events_jsonl.py --help`
- `python scripts/migration/compat_dump_request_bodies.py --help`
- `python scripts/migration/recover_missing_files_from_codex_outputs.py --help`

### You are doing parity or target-drift work

Start with:

- `python scripts/research/parity/audit_e4_target_drift.py --help`
- `python scripts/research/parity/check_e4_snapshot_coverage.py --json`

Use these when the task is research-facing and specifically about parity,
target drift, or E4 snapshot coverage.

## Canonical path policy

When you write:

- docs
- operator notes
- quickstarts
- helper scripts
- CI snippets

prefer the categorized canonical path, not the legacy top-level wrapper path.

Examples:

- use `scripts/release/export_cli_bridge_contracts.py`
  not `scripts/export_cli_bridge_contracts.py`
- use `scripts/ops/guardrail_metrics.py`
  not `scripts/guardrail_metrics.py`
- use `scripts/research/parity/audit_e4_target_drift.py`
  not `scripts/audit_e4_target_drift.py`

The legacy paths still exist only to avoid breaking callers during migration.
They are not the preferred public surface anymore.

## A good operator reading path

If you are new to the repo but not new to operating systems like this, use this
sequence:

1. [../getting-started/INSTALL_AND_DEV_QUICKSTART.md](../getting-started/INSTALL_AND_DEV_QUICKSTART.md)
2. `python scripts/dev/first_time_doctor.py --strict`
3. [../reference/SCRIPTS_INDEX.md](../reference/SCRIPTS_INDEX.md)
4. the specific family above that matches your task

That route gives you the shortest path from "what is this repo?" to "which
entrypoint should I actually run?"

