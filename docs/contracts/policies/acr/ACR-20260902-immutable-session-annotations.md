# ACR-20260902-immutable-session-annotations

- `acr_id`: `ACR-20260902-immutable-session-annotations`
- `title`: Retain immutable annotations with Product Session
- `author`: DSH donor implementation campaign
- `date`: 2026-09-02
- `status`: implemented

## 1) Problem Statement

Research labels need durable message and trajectory identity without rewriting transcript bytes or creating a second annotation authority. Internal annotation events must replay deterministically while remaining absent from the frozen public event contract until its amendment gate completes.

## 2) Scope and Surfaces

- Kernel modules touched: Product Session event ownership, Session runner identity emission, and public event projection.
- Extension modules touched: none.
- Contract surfaces touched: internal annotation records and hidden-event pagination only.
- Is this a **kernel danger-zone** change? `yes`
- Danger-zone: yes.
- Outside scope: public schemas, generated bindings, SDK event kinds, TUI rendering, provider behavior, publication, and signing.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no.
- Cross-harness parity narrowed: no.
- Default endpoint semantics altered: no. Annotation rows are filtered before public projection, and visible event pagination preserves its existing limit contract.
- Coupling risk score: `medium`. The change joins canonical message identity, replay validation, and public projection filtering at existing owners.

## 4) Change Classification

- Classification: `internal`
- Compatibility window: clean cutover; no public annotation kind exists in this change.
- Required schema/version bumps: none. Public exposure requires the separate W9 amendment, schema, generator, SDK, TUI, installed-composition, and E4 gates.

## 5) Evidence and Validation Plan

- Required contract lane tests: Product Session replay, Session runner event identity, public projection generation, public Session operations, and SSE pagination across hidden rows.
- Required security tests: annotation payload never changes or exposes message content; unknown and duplicate identities fail before append and during replay.
- Required evidence: focused combined test run, exact-head independent review, and protected PR checks.
- Acceptance criteria:
  - Annotation identity, target, label, author, and generation survive restore.
  - Canonical message and trajectory identities are paired and unique.
  - Transcript and model-input content bytes remain unchanged.
  - Public event readers omit internal annotations and continue to the requested visible limit or durable exhaustion.
  - No public schema, registry, SDK, generated binding, or TUI contract changes.

## 6) Rollout Plan

- Rollout phases: merge the internal owner through protected checks; keep projection hidden; expose only after W9 public amendment and downstream conformance gates.
- Flags/toggles: none.
- Blast radius constraints: Product Session event storage and public read pagination only.
- Monitoring hooks: focused replay/projection tests and installed composition proof at W9.

## 7) Rollback Plan

- Trigger conditions: replay divergence, message-byte mutation, annotation leakage through public projection, pagination truncation, or protected check failure.
- Exact rollback commands: revert the protected-main merge with `git revert -m 1 <merge-sha>`; do not force-push `main`.
- Artifact/state restoration steps: no schema or data migration exists; restore the preceding package artifacts.
- Post-rollback verification: rerun Product Session replay, public event projection, and public Session pagination tests.

## 8) Approvals

- Kernel reviewer: independent exact-head review required.
- Contracts reviewer: protected compatibility and danger-zone gates required.
- Ops reviewer: protected branch checks required.
- Final decision: protected branch checks and repository merge policy control merge.
