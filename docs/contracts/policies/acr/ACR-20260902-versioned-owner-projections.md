# ACR-20260902-versioned-owner-projections

- `acr_id`: `ACR-20260902-versioned-owner-projections`
- `title`: Version projections at their existing event owners
- `author`: DSH donor implementation campaign
- `date`: 2026-09-02
- `status`: implemented

## 1) Problem Statement

Session and coordination callers need replay, snapshot, and live projections with explicit version and source lineage. Repeating folds in consumers loses owner semantics and compound coordination cursors cannot be represented honestly by one scalar maximum.

## 2) Scope and Surfaces

- Kernel modules touched: Product Session events, WorkItem events, coordination views, and their internal exports.
- Extension modules touched: none.
- Contract surfaces touched: internal owner projection helpers and immutable projection result values.
- Is this a **kernel danger-zone** change? `yes`
- Danger-zone: yes.
- Outside scope: public schemas, SDKs, TUI behavior, persistence formats, provider behavior, publication, and signing.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no.
- Cross-harness parity narrowed: no.
- Default endpoint semantics altered: no. Existing live folds remain the behavioral oracle.
- Coupling risk score: `medium`. Version and lineage are attached to existing folds; compound coordination lineage retains one exact cursor per stream.

## 4) Change Classification

- Classification: `internal`
- Compatibility window: clean cutover. The duplicate coordination rebuild fold delegates to the retained owner projector.
- Required schema/version bumps: none.

## 5) Evidence and Validation Plan

- Required contract lane tests: Product Session replay/snapshot/live equality, WorkItem projections, coordination rebuilds, and heterogeneous compound cursors.
- Required security tests: none beyond existing immutable event-owner boundaries; no new external input is parsed.
- Required evidence: focused owner test run, exact-head independent review, and protected PR checks.
- Acceptance criteria:
  - Replay, snapshot, and live paths return equal values at the same owner head.
  - Every result names a projector version and immutable source lineage.
  - Compound coordination lineage retains exactly one cursor for every required stream.
  - Missing, duplicate, unknown, and mismatched cursors fail with typed projection errors.
  - No duplicate fold, projection registry, public schema, or compatibility path remains.

## 6) Rollout Plan

- Rollout phases: merge internal owner helpers through protected checks; consume them from the composed research command only after its packet opens.
- Flags/toggles: none.
- Blast radius constraints: internal Product Session and coordination projections only.
- Monitoring hooks: focused projection equality and owner regression tests.

## 7) Rollback Plan

- Trigger conditions: live/replay/snapshot mismatch, cursor acceptance outside the exact stream set, owner event mutation, or protected check failure.
- Exact rollback commands: revert the protected-main merge with `git revert -m 1 <merge-sha>`; do not force-push `main`.
- Artifact/state restoration steps: none; no persistent schema or data migration exists.
- Post-rollback verification: rerun Product Session, WorkItem, and coordination owner tests.

## 8) Approvals

- Kernel reviewer: independent exact-head review required.
- Contracts reviewer: protected compatibility and danger-zone gates required.
- Ops reviewer: protected branch checks required.
- Final decision: protected branch checks and repository merge policy control merge.
