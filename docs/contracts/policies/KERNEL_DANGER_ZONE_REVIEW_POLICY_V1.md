# Kernel Danger-Zone Review Policy V1

## Purpose

Protect BreadBoard generalization by forcing explicit review for changes that can silently alter kernel behavior, contract semantics, or parity guarantees.

## Kernel danger-zone definition

A change is in danger-zone scope if it touches one or more of:

1. Core runtime and orchestration modules in:
   - `breadboard/**`
   - `agentic_coder_prototype/**`
   - `breadboard_sdk/**`
   - `tool_calling/**`
2. Contract schema sets in:
   - `docs/conformance/schemas/**`
   - `docs/contracts/**`
3. Replay/parity/contract guardrails in:
   - `scripts/validate_kernel_ext_boundaries.py`
   - `scripts/run_conformance_matrix.py`
   - `scripts/check_replay_determinism_gate.py`

## Required review package

For danger-zone changes, PRs must include:

1. Completed ACR using:
   - `docs/contracts/policies/ARCHITECTURE_CHANGE_REQUEST_TEMPLATE_V1.md`
2. Evidence links:
   - Contract lane output (schema/fixture and CT scenarios)
   - Replay determinism gate result
   - Relevant evidence bundle validation output
3. Rollback checklist completed:
   - `docs/contracts/policies/ROLLBACK_PROTOCOL_CHECKLIST_V1.md`

## Mandatory gates

Danger-zone PR is not mergeable until all are true:

1. Kernel/ext import boundary validator passes.
2. Conformance wave-A gate passes.
3. New/updated tests for any behavior change are present and passing.
4. Breaking changes have explicit versioning and migration plan.

## Decision outcomes

- `approve`: all gates pass and risks accepted.
- `approve_with_conditions`: explicitly listed follow-ups must be tracked.
- `reject`: coupling risk or evidence gap is unresolved.
