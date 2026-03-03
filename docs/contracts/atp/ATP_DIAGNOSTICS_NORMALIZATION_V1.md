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

ATP retrieval decomposition runner emits diagnostics artifacts and validation status:

- script: `scripts/run_atp_retrieval_decomposition_loop_v1.py`
- artifact: `diagnostics.json`
- report fields:
  - `diagnostic_taxonomy_version`
  - `diagnostics_path`
  - `diagnostics_validation_ok`
  - `diagnostics_unknown_count`

## Unknown/Fallback Policy

- Any unmapped diagnostic must be labeled `unknown`.
- `unknown` diagnostics are never dropped.
- `diagnostics_unknown_count` is tracked for taxonomy refinement.

## Extension Boundary Note

Diagnostic normalization is ATP extension-layer behavior; kernel ABI/contracts remain domain-agnostic.
