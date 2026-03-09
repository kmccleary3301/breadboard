# BreadBoard Kernel Program Index V1

This index is the quickest route through the shared-kernel / multi-engine program.

## Start here

1. `docs/contracts/kernel/KERNEL_DOCTRINES_V1.md`
2. `docs/contracts/kernel/semantics/README.md`
3. `docs/contracts/kernel/KERNEL_EVENT_FAMILY_REGISTRY_V1.md`
4. `docs/contracts/kernel/PYTHON_SERVICE_BOUNDARY_MATRIX_V1.md`
5. `docs/contracts/kernel/HYBRID_DELEGATION_BOUNDARIES_V1.md`
6. `contracts/kernel/README.md`
7. `conformance/engine_fixtures/README.md`

## Semantic dossiers drafted so far

- run request / run context
- kernel event
- session transcript
- provider exchange
- tool lifecycle and render
- permission and guardrails
- middleware lifecycle
- task and subagent
- checkpoint and longrun
- replay session
- conformance evidence

## Machine-readable scaffold

- `contracts/kernel/schemas/`
- `contracts/kernel/examples/`
- `contracts/kernel/manifests/`

## Engine-conformance scaffold

- `conformance/engine_fixtures/COMPARATOR_CLASSES_V1.md`
- `conformance/engine_fixtures/python_reference_manifest_v1.json`
- `scripts/compare_kernel_conformance_engines.py`
- `scripts/run_kernel_conformance_gate.py`

## Hybrid / proving-ground readiness

- `docs/contracts/kernel/HYBRID_DELEGATION_BOUNDARIES_V1.md`
- `docs/contracts/kernel/PYTHON_SERVICE_BOUNDARY_MATRIX_V1.md`
- `docs/contracts/kernel/OPENCLAW_PROVING_GROUND_READINESS_V1.md`

## Current tranche status

Current state is strong on:

- kernel doctrine
- contract inventory
- first semantic dossiers
- first schema pack scaffolds
- first engine-fixture scaffolds
- first Python reference-engine boundary clarifications

Current state is still intentionally early on:

- the last full Python reference-engine event-family sweep
- comparator-grade cross-engine runtime execution, beyond shared fixture consumption
- TypeScript engine implementation beyond the current constrained execution slice
- hybrid proving-ground execution
