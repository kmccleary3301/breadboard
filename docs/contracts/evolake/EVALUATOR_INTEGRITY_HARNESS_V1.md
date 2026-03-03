# EvoLake Evaluator Integrity Harness v1

Date: 2026-02-24

## Purpose

Detect evaluator nondeterminism and quarantine flaky candidates before claims are accepted.

## Report Contract

Schema:

- `docs/contracts/evolake/schemas/evaluator_integrity_report_v1.schema.json`

Emitter:

- `scripts/run_evolake_evaluator_integrity_harness_v1.py`
- `scripts/summarize_evolake_evaluator_integrity_v1.py`
- `scripts/check_evolake_quarantine_enforcement_v1.py`

## Nondeterminism Detection Criteria

Given a sampled candidate and repeated reevaluations:

- `spread = max(score_runs) - min(score_runs)`
- candidate is nondeterministic if `spread > score_tolerance`

Global flake rate:

- `flake_rate = nondeterministic_candidates / sampled_candidates`

Pass condition:

- `flake_rate <= max_flake_rate`

## Quarantine Policy

- Any nondeterministic candidate is quarantined.
- Quarantined candidates must not be used for claim acceptance or campaign promotion.
- Quarantine list is emitted in:
  - `quarantined_candidates`
  - `nightly_reporting.quarantine_candidates`
  - `quarantine_candidates.latest.json` artifact

## Nightly Reporting Fields

The report includes explicit nightly rollup fields:

- `nightly_reporting.quarantine_count`
- `nightly_reporting.quarantine_candidates`
- `hermetic_mode`
- `hermetic_fingerprint`

These fields are intended for CI/nightly summaries and trend dashboards.
