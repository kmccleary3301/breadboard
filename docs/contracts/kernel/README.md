# BreadBoard Kernel Contracts

This directory is the documentation home for the shared BreadBoard kernel contract program.

The kernel contract is the future cross-language truth surface for BreadBoard. It is intended to govern:

- Python reference-engine behavior
- TypeScript embedded-engine behavior
- replay and conformance fixtures
- host projection boundaries
- evidence-backed support claims

The goal is not to make every implementation detail identical. The goal is to make all **shared runtime semantics** explicit enough that multiple interpreters can conform to the same runtime system without silently drifting.

---

## What belongs here

This directory should hold:

- kernel doctrines
- semantic dossiers for shared contract families
- cross-language runtime boundaries
- explanations of what is shared vs engine-private vs projection-only

This directory should **not** hold:

- host-specific reducer or UI projection contracts as if they were kernel truth
- one-off experimental notes that are not part of the contract program
- implementation-detail notes that are purely Python-private or TS-private

---

## Core doctrine

Start here:

- [KERNEL_DOCTRINES_V1.md](KERNEL_DOCTRINES_V1.md)
- [KERNEL_EVENT_FAMILY_REGISTRY_V1.md](KERNEL_EVENT_FAMILY_REGISTRY_V1.md)
- [PYTHON_SERVICE_BOUNDARY_MATRIX_V1.md](PYTHON_SERVICE_BOUNDARY_MATRIX_V1.md)
- [HYBRID_DELEGATION_BOUNDARIES_V1.md](HYBRID_DELEGATION_BOUNDARIES_V1.md)
- [ORCHESTRATION_BACKEND_DECISION_V1.md](ORCHESTRATION_BACKEND_DECISION_V1.md)
- [PROGRAM_INDEX_V1.md](PROGRAM_INDEX_V1.md)

Then read:

- [semantics/README.md](semantics/README.md)

Machine-readable scaffolds live under:

- [../../../contracts/kernel/README.md](../../../contracts/kernel/README.md)
- [../../../conformance/engine_fixtures/README.md](../../../conformance/engine_fixtures/README.md)

---

## Relationship to other docs

### `docs/CONTRACT_SURFACES.md`

This is the top-level map of stable contract boundaries. It should point here when kernel contracts become the primary runtime source of truth.

### `docs/PARITY_KERNEL_BOUNDARIES.md`

This remains useful for parity-critical surfaces and current E4/emulation governance. The kernel-contract program is broader: it is about multi-engine runtime truth, not only target harness parity.

### `contracts/kernel/`

This is where machine-readable contract artifacts should live once they exist. This `docs/contracts/kernel/` directory is the human-readable semantic and doctrinal counterpart.

### `conformance/engine_fixtures/`

This is where cross-engine contract fixtures and related README/docs should live.

### Hybrid / proving-ground docs

The first hybrid-delegation and proving-ground boundaries live alongside the kernel docs so they are constrained by the same doctrine instead of becoming host-driven design drift.

---

## Immediate intended contract families

The initial contract families are:

1. run request / run context
2. kernel event log
3. session / transcript snapshot
4. provider exchange IR
5. tool lifecycle and tool render
6. permission / approval / guardrail record
7. middleware / hook lifecycle
8. task / subagent / background task
9. checkpoint / longrun state
10. execution capability / placement
11. execution driver policy / evidence expectations
12. sandbox request / result
13. distributed task descriptor
14. transcript continuation patch
15. unsupported-case taxonomy
16. replay session
17. conformance evidence
18. import / export session
19. projection adapter boundaries

---

## Editing policy

When adding or changing kernel semantics:

1. update doctrine or semantics docs first
2. update machine-readable contract artifacts second
3. update fixtures and evidence third
4. update engine implementations last

This order is not optional if we want to avoid language-specific drift.
