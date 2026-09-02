# ACR-20260902-durable-logical-event-truth

- `acr_id`: `ACR-20260902-durable-logical-event-truth`
- `title`: Make logical session admission and settlement durable
- `author`: DSH donor implementation campaign
- `date`: 2026-09-02
- `status`: implemented

## 1) Problem Statement

A logical session could acknowledge admission before its event was durable, lose accepted work across a process crash, or leave its durable journal unsettled after the runner had already reached a terminal state. Recovery then depended on transient process state rather than the durable product event stream.

## 2) Scope and Surfaces

- Kernel modules touched: session service admission/recovery, session runner settlement, public session event schema, and session replay.
- Extension modules touched: none.
- Contract surfaces touched: existing session event serialization and existing public session readback.
- Is this a **kernel danger-zone** change? `yes`
- Danger-zone: yes.
- Outside scope: provider request shape, execution worlds, package publication, signing, and deployment.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no.
- Cross-harness parity narrowed: no.
- Default endpoint semantics altered: no. The existing public session endpoint now reads the canonical durable journal after recovery instead of failing on missing transient state.
- Coupling risk score: `medium`. Admission and settlement now share one durable event authority across the service and runner lifecycle.

## 4) Change Classification

- Classification: `internal`
- Compatibility window: clean cutover. Existing public schemas and SDK method signatures are unchanged.
- Required schema/version bumps: none.

## 5) Evidence and Validation Plan

- Required contract lane tests: event schema stability, session replay, pending-permission recovery, public session readback, and runner/service prewarm reconciliation.
- Required security tests: admission durability before acknowledgement and idempotent recovery without duplicate settlement.
- Required evidence: exact-head independent review, protected PR checks, and focused changed-area tests.
- Acceptance criteria:
  - Accepted input is durable before the command acknowledges admission.
  - Every admitted operation reaches exactly one durable terminal settlement.
  - Recovery replays the canonical journal and preserves ordering, correlation, and typed payloads.
  - Recovery reconciles an already-terminal runner before starting more work.
  - Public session readback uses the same durable authority.

## 6) Rollout Plan

- Rollout phases: merge through protected PR checks, rebuild the installed engine artifact from merged `main`, and rerun the cross-client session journey.
- Flags/toggles: none.
- Blast radius constraints: Product Session admission, recovery, replay, and settlement only.
- Monitoring hooks: public session readback, replay determinism, event-envelope snapshot, and installed client smoke lanes.

## 7) Rollback Plan

- Trigger conditions: duplicate settlement, missing admitted input after restart, replay divergence, or protected checks failing.
- Exact rollback commands: revert the protected-main merge with `git revert -m 1 <merge-sha>`; do not force-push `main`.
- Artifact/state restoration steps: restore the preceding clean engine wheel and installed evidence bundle. No schema migration is required.
- Post-rollback verification: rerun focused session recovery, replay, pending-permission, and public readback tests.

## 8) Approvals

- Kernel reviewer: independent exact-head correctness review completed.
- Contracts reviewer: compatibility is reviewed as non-breaking; protected contract gates control merge.
- Ops reviewer: protected danger-zone policy and security checks control merge.
- Final decision: protected branch checks and repository merge policy control merge.
