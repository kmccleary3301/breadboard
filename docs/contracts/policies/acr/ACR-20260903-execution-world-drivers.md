# ACR-20260903-execution-world-drivers

- `acr_id`: `ACR-20260903-execution-world-drivers`
- `title`: Centralize execution-world driver selection and terminal leases
- `author`: DSH donor implementation campaign
- `date`: 2026-09-03
- `status`: implemented

## 1) Problem Statement

Local, OCI, and remote execution drivers need one deterministic owner for eligibility, explicit pinning, fallback, timeout, terminal-session lifecycle, and cleanup. Duplicated selection rules permit capability drift and ambiguous execution-world ownership.

## 2) Scope and Surfaces

- Kernel modules touched: TypeScript execution-driver registry, local/OCI/remote drivers, Backbone integration, and terminal-session ownership.
- Extension modules touched: existing execution-driver packages only.
- Contract surfaces touched: internal driver capabilities, ordered selection, explicit pins, terminal leases, and driver error taxonomy.
- Is this a **kernel danger-zone** change? `yes`
- Danger-zone: yes.
- Outside scope: changing Product Session event semantics, adding execution worlds, public protocol schemas, provider behavior, and credentials.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no. Backbone consumes the shared execution-driver package.
- Cross-harness parity narrowed: no.
- Default endpoint semantics altered: no; prior local/OCI/remote capabilities remain, with deterministic ownership.
- Coupling risk score: `medium`. Selection moves to one existing package and drivers retain backend-specific execution.

## 4) Change Classification

- Classification: `internal`, non-breaking.
- Compatibility window: clean cutover; duplicate Backbone selection is removed.
- Required schema/version bumps: none.
- Design decision: shared `@breadboard/execution-drivers` Design 2 owns predicates, pins, fallback, timeout, and terminal lease arbitration.

## 5) Evidence and Validation Plan

- Required contract lane tests: shared selection, local process, OCI container, remote HTTP, Backbone integration, and terminal-session lifecycle.
- Required security tests: OCI network isolation, explicit pin rejection, remote timeout through response-body decode, and cleanup after failure.
- Required evidence: focused package tests, exact-head independent review, zero unresolved review threads, and protected PR checks.
- Acceptance criteria:
  - Candidate ordering and explicit pins are deterministic.
  - Unsupported terminal-session pins fail before backend execution.
  - Concurrent starts cannot create duplicate terminal sessions.
  - Fetch and response-body decode share the same timeout boundary.
  - Every driver publishes one structured execution receipt and authenticates cleanup.

## 6) Rollout Plan

- Rollout phases: merge shared ownership and driver adapters through protected checks; use the installed comparison command in W10 for four-world evidence.
- Flags/toggles: none.
- Blast radius constraints: TypeScript execution-driver and Backbone packages only.
- Monitoring hooks: focused driver conformance tests and later installed local/container/Ray/Slurm comparison.

## 7) Rollback Plan

- Trigger conditions: driver mis-selection, terminal duplication, timeout escape, cleanup failure, or protected check failure.
- Exact rollback commands: revert the protected-main merge with `git revert -m 1 <merge-sha>`; do not force-push `main`.
- Artifact/state restoration steps: no persistent schema migration exists; restore the preceding package artifacts.
- Post-rollback verification: rerun shared driver, local, OCI, remote, and Backbone test suites.

## 8) Approvals

- Kernel reviewer: independent exact-head review required.
- Contracts reviewer: protected compatibility and danger-zone gates required.
- Ops reviewer: protected branch checks required.
- Final decision: protected branch checks and repository merge policy control merge.
