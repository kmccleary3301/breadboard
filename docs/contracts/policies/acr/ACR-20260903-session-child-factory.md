# ACR-20260903-session-child-factory

- `acr_id`: `ACR-20260903-session-child-factory`
- `title`: Own child execution and recovery beneath Product Session
- `author`: DSH donor implementation campaign
- `date`: 2026-09-03
- `status`: implemented

## 1) Problem Statement

Child work must start, settle, cancel, and recover under one Session-centered authority. Direct process or Ray launches without durable intent can orphan work, duplicate execution after a crash, or diverge from the Product Session journal.

## 2) Scope and Surfaces

- Kernel modules touched: Product Session child factory, WorkItem coordination, CLI bridge persistence, process execution, and Ray execution.
- Extension modules touched: none.
- Contract surfaces touched: internal child identity, launch intent, settlement, cancellation, and recovery records.
- Is this a **kernel danger-zone** change? `yes`
- Danger-zone: yes.
- Outside scope: public workflow schemas, provider behavior, SDK/TUI exposure, deployment, and direct WorkItem cancellation as a public orchestration path.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no.
- Cross-harness parity narrowed: no.
- Default endpoint semantics altered: no; service operations route through the Session-centered factory.
- Coupling risk score: `high`. Durable journals coordinate process/Ray lifecycle and Product Session replay across crash boundaries.

## 4) Change Classification

- Classification: `internal`, non-breaking.
- Compatibility window: clean cutover; service child operations use the factory owner.
- Required schema/version bumps: none.
- Design decision: Session-centered child factory owns launch and tree cancellation. Existing projector owners retain read-model projection.

## 5) Evidence and Validation Plan

- Required contract lane tests: Product Session replay, WorkItem semantic journal, process and Ray launch/recovery, settlement idempotency, cancellation, and registry persistence.
- Required security tests: strict journal field types, cross-platform record locks, path/process identity verification, and no execution on wrapper EOF.
- Required evidence: focused child/WorkItem/Session suites, deterministic crash-window repros, exact-head independent review, and protected PR checks.
- Acceptance criteria:
  - Intent is durable before external child execution.
  - Recovery never duplicates an already accepted process or Ray invocation.
  - Wrapper EOF cannot execute the configured command.
  - Complete corrupt journal frames fail typed and are never truncated.
  - Settlement is idempotent while conflicting explicit references fail typed.
  - Tree cancellation routes through the Session factory and survives restart.

## 6) Rollout Plan

- Rollout phases: merge the internal child owner through protected checks; expose public workflow operations only through the separate W7.5/W7.6 gates.
- Flags/toggles: none.
- Blast radius constraints: Product Session child operations, coordination journal, process/Ray adapters, and registry lock implementation.
- Monitoring hooks: crash-window, no-orphan, no-duplicate, replay, cancellation, and portability tests.

## 7) Rollback Plan

- Trigger conditions: duplicate child execution, orphaned process/actor, replay divergence, lock-integrity failure, cancellation loss, or protected check failure.
- Exact rollback commands: revert the protected-main merge with `git revert -m 1 <merge-sha>`; do not force-push `main`.
- Artifact/state restoration steps: preserve existing semantic journals as evidence; restore the preceding runtime package artifacts.
- Post-rollback verification: rerun Product Session, child factory, WorkItem journal, registry, process, and Ray tests.

## 8) Approvals

- Kernel reviewer: independent exact-head review required.
- Contracts reviewer: protected compatibility and danger-zone gates required.
- Ops reviewer: protected branch checks required.
- Final decision: protected branch checks and repository merge policy control merge.
