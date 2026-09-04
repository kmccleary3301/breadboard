# ACR-20260903-execution-world-drivers

- `acr_id`: `ACR-20260903-execution-world-drivers`
- `title`: Centralize execution-world selection, evidence, and remote adapters
- `author`: DSH donor implementation campaign
- `date`: 2026-09-03
- `status`: implemented

## 1) Problem Statement

Local process, OCI container, Ray actor, and Slurm job execution need one deterministic owner for eligibility, explicit pinning, fallback, timeout, provider-neutral results, durable evidence, terminal-session lifecycle, and cleanup. Duplicated selection and evidence rules permit capability drift and ambiguous execution-world ownership.

## 2) Scope and Surfaces

- Kernel modules touched: TypeScript execution-driver registry, local/OCI/remote drivers, host bridges, Backbone integration, and terminal-session ownership.
- Extension modules touched: existing execution-driver packages only.
- Contract surfaces touched: internal driver capabilities, ordered selection, explicit pins, terminal leases, canonical sandbox results, content-addressed evidence, and driver error taxonomy.
- Is this a **kernel danger-zone** change? `yes`
- Danger-zone: yes.
- Outside scope: changing Product Session event semantics, public protocol schemas, provider behavior, credentials, or RL execution.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no. Backbone consumes the shared execution-driver package.
- Cross-harness parity narrowed: no.
- Default endpoint semantics altered: no; existing local/OCI/remote selection remains deterministic, while Ray and Slurm implement the same internal world contract.
- Coupling risk score: `medium`. Selection and canonical evidence move to one existing package; drivers retain backend-specific execution and cleanup.

## 4) Change Classification

- Classification: `internal`, non-breaking.
- Compatibility window: clean cutover; duplicate Backbone selection is removed.
- Required schema/version bumps: none.
- Design decision: shared `@breadboard/execution-drivers` Design 2 owns predicates, pins, fallback, timeout, terminal lease arbitration, canonical results, provider-neutral validation, and shared content-addressed evidence.

## 5) Evidence and Validation Plan

- Required contract lane tests: shared selection/evidence, local process, OCI container, Ray scheduled execution, Slurm scheduled execution, remote HTTP, Backbone integration, and terminal-session lifecycle.
- Required security tests: OCI network isolation, explicit pin rejection, remote timeout through response-body decode, bounded Slurm submission/observation/cancellation, artifact-store ownership and symlink rejection, and cleanup after failure.
- Required evidence: focused package tests, actual local/container/Ray/Slurm executions of one fixed request, exact-head independent review, zero unresolved review threads, and protected PR checks.
- Acceptance criteria:
  - Candidate ordering and explicit pins are deterministic.
  - Unsupported terminal-session pins fail before backend execution.
  - Concurrent starts cannot create duplicate terminal sessions.
  - Fetch and response-body decode share the same timeout boundary.
  - The four execution worlds publish the same provider-neutral result and content-addressed output identity for the fixed request.
  - Scheduled evidence is serialized, required, retried in order, and contains no provider-specific fields.
  - Slurm submission receipts retain the raw acknowledgement, and lost acknowledgements reconcile by durable request identity before cleanup.

## 6) Rollout Plan

- Rollout phases: merge shared ownership, canonical evidence, and all four world adapters through protected checks; compose those drivers behind the installed comparison command in W10.
- Flags/toggles: none.
- Blast radius constraints: TypeScript execution-driver, host-bridge, and Backbone packages only.
- Monitoring hooks: focused driver conformance tests plus retained actual-world evidence in the W5 DIT.

## 7) Rollback Plan

- Trigger conditions: driver mis-selection, terminal duplication, result/evidence divergence, timeout escape, lost-ack duplication, cleanup failure, or protected check failure.
- Exact rollback commands: revert the protected-main merge with `git revert -m 1 <merge-sha>`; do not force-push `main`.
- Artifact/state restoration steps: no public schema migration exists; retain immutable execution evidence and restore the preceding package artifacts.
- Post-rollback verification: rerun shared driver, local, OCI, remote, and host-bridge test suites plus the actual-world comparison.

## 8) Approvals

- Kernel reviewer: implementation head `ac842ece18a63ab1be7456663566030a515e3975` independently reviewed with zero findings at confidence 0.97.
- Contracts reviewer: protected compatibility and danger-zone gates required.
- Ops reviewer: protected branch checks and retained actual Ray/Slurm evidence required.
- Final decision: protected branch checks and repository merge policy control merge.
