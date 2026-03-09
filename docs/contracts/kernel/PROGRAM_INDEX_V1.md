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
8. `docs/contracts/kernel/ORCHESTRATION_BACKEND_DECISION_V1.md`

## Semantic dossiers drafted so far

- run request / run context
- backbone API
- kernel event
- session transcript
- provider exchange
- support claim and projection profile
- workspace layer
- host kit
- tool lifecycle and render
- permission and guardrails
- middleware lifecycle
- task and subagent
- checkpoint and longrun
- execution capability and placement
- execution driver policy and evidence
- sandbox envelopes
- distributed task descriptor
- durable orchestration and resume
- transcript continuation patch
- unsupported case taxonomy
- replay session
- conformance evidence

## Machine-readable contract pack

- `contracts/kernel/schemas/`
- `contracts/kernel/examples/`
- `contracts/kernel/manifests/`

## Engine-conformance program

- `conformance/engine_fixtures/COMPARATOR_CLASSES_V1.md`
- `conformance/engine_fixtures/python_reference_manifest_v1.json`
- `scripts/compare_kernel_conformance_engines.py`
- `scripts/run_kernel_conformance_gate.py`

## Hybrid / proving-ground readiness

- `docs/contracts/kernel/HYBRID_DELEGATION_BOUNDARIES_V1.md`
- `docs/contracts/kernel/PYTHON_SERVICE_BOUNDARY_MATRIX_V1.md`
- `docs/contracts/kernel/ORCHESTRATION_BACKEND_DECISION_V1.md`
- `docs/contracts/kernel/OPENCLAW_PROVING_GROUND_READINESS_V1.md`
- `docs/contracts/kernel/OPENCLAW_HOST_BRIDGE_V1.md`
- `docs/contracts/kernel/OPENCLAW_HOST_BRIDGE_ACCEPTANCE_V1.md`
- `docs/contracts/kernel/TS_PRIMARY_HOST_PATH_READINESS_V1.md`

## TS package surfaces

- `sdk/ts-kernel-contracts/`
- `sdk/ts-kernel-core/`
- `sdk/ts-backbone/`
- `sdk/ts-workspace/`
- `sdk/ts-host-kits/`
- `sdk/ts-transport-ai-sdk/`
- `sdk/ts-host-t3/`
- `sdk/ts-host-bridges/`
- `sdk/ts-execution-drivers/`
- `sdk/ts-execution-driver-local/`
- `sdk/ts-execution-driver-oci/`
- `sdk/ts-execution-driver-remote/`
- `sdk/ts-orchestration-temporal/`

## Current tranche status

Current state is strong on:

- kernel doctrine
- contract inventory
- semantic dossiers for the scoped V2 runtime families
- machine-readable contract pack
- engine-fixture and manifest program
- Python reference-engine boundary clarifications
- the V2 execution-capability contract family
- the execution-driver and orchestration backend family
- delegated remote execution path
- the Python-free primary TS host boundary for supported slices
- the OpenClaw proving-ground seam for the scoped supported slices

Current state is intentionally bounded on:

- broad host/runtime parity beyond the frozen supported slices
- microVM-backed or stronger delegated execution paths
- production-grade orchestration deployment and fleet management
- full Pi/OpenClaw replacement claims
