# BreadBoard ATP/EvoLake P2 Decision Protocol (V1)

Date: 2026-02-23

## Scope

This protocol governs ATP/EvoLake changes that can affect BreadBoard kernel generalization guarantees.

## Decision Artifact Requirements

Any change touching kernel danger-zone paths must include one of:

- `docs/contracts/policies/acr/ACR-YYYYMMDD-<slug>.md`
- `docs/contracts/policies/adr/ADR-YYYYMMDD-<slug>.md`

## Required Fields (ACR/ADR)

1. decision id
2. problem statement
3. alternatives considered
4. invariants impacted
5. compatibility and rollback plan
6. required evidence artifacts
7. acceptance criteria

## Approval Gates

1. danger-zone CI guard passes
2. request-body/tool-schema/event-envelope drift gates pass
3. extension-disabled and extension-enabled lanes pass
4. replay determinism and evidence contract guards pass

## Rollback Trigger Conditions

- broken default endpoint semantics
- byte/hash drift on parity-critical surfaces without approved migration
- evidence bundle integrity failures for release claims

## Execution Rule

No danger-zone PR may merge without explicit ACR/ADR evidence and passing CI enforcement.
