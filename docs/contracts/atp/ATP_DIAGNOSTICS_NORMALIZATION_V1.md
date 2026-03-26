# ATP Diagnostics Normalization v1

Date: 2026-02-24

## Purpose

Normalize verifier/runtime diagnostic outputs into bounded classes for stable logging, analytics, and repair-policy routing.

## Taxonomy

Version: `atp_diagnostic_taxonomy_v1`

Allowed classes:

- `syntax_error`
- `type_error`
- `tactic_failure`
- `timeout`
- `resource_limit`
- `missing_import`
- `goal_mismatch`
- `proof_incomplete`
- `nondeterminism_detected`
- `unknown` (required fallback)

## Contract

Schema:

- `docs/contracts/atp/schemas/atp_diagnostic_record_v1.schema.json`

Runtime mapping module:

- `agentic_coder_prototype/atp_diagnostics.py`

Primary interface:

- `normalize_diagnostic_class(raw_code, message) -> class`
- `build_diagnostic_record(...) -> atp_diagnostic_record_v1`

## Runtime Emission Path (Current)

The current merged ATP program emits ATP-facing diagnostics primarily through the adapter slice and formal-pack runners:

- adapter slice:
  - script: `scripts/run_bb_atp_adapter_slice_v1.py`
  - per-task artifact: `runner_diagnostic.json`
  - run-level payload schema: `breadboard.bb_atp_adapter_slice_run.v2`
- formal pack:
  - script: `scripts/run_bb_formal_pack_v1.py`
  - per-task artifact family includes raw diagnostic payloads emitted during formal-pack execution
  - run-level payload schema: `breadboard.bb_formal_pack_run.v1`

Earlier docs referenced a standalone ATP retrieval/decomposition runner. On current `main`, the adapter-slice and formal-pack paths are the maintained ATP-facing emission routes.

## Unknown/Fallback Policy

- Any unmapped diagnostic must be labeled `unknown`.
- `unknown` diagnostics are never dropped.
- `diagnostics_unknown_count` is tracked for taxonomy refinement.

## Extension Boundary Note

Diagnostic normalization is ATP extension-layer behavior; kernel ABI/contracts remain domain-agnostic.
