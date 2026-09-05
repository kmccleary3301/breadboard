# ACR-20260903-lossless-session-compaction

- `acr_id`: `ACR-20260903-lossless-session-compaction`
- `title`: Preserve exact effective context and raw facts across compaction
- `author`: DSH donor implementation campaign
- `date`: 2026-09-03
- `status`: implemented

## 1) Problem Statement

Long-running sessions lacked one durable compaction boundary. Context snapshots and C-Tree facts could be observed separately, but replay could not prove that the model-facing context reconstructed byte-for-byte or that successive compactions retained every prior raw fact.

## 2) Scope and Surfaces

- Kernel modules touched: Product Session events/replay, SessionState snapshot ownership, and public event filtering.
- Extension modules touched: none.
- Contract surfaces touched: the internal `bb.session_event.v1` journal gains `context.compacted`; the public event revision remains unchanged and filters this kind pending a public amendment.
- Is this a **kernel danger-zone** change? `yes`
- Danger-zone: yes.
- Outside scope: summarization policy, storage spill, retrieval ranking, and public SDK bindings.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no.
- Cross-harness parity narrowed: no.
- Default endpoint semantics altered: no. Compaction events are internal and do not consume public pagination limits.
- Coupling risk score: `medium`. SessionState owns the pre-compaction model-facing bytes and raw C-Tree identities; Product Session owns their durable replay boundary.

## 4) Change Classification

- Classification: `internal`
- Compatibility window: clean cutover. Existing public schemas and SDK method signatures are unchanged.
- Required schema/version bumps: none for public contracts; the internal event reader and writer land atomically.

## 5) Evidence and Validation Plan

- Required contract lane tests: Product Session transition/replay tests, SessionState owner snapshot tests, and hidden-event public pagination tests.
- Required security tests: strict payload validation, digest verification, monotonic raw-fact retention, and invalid source-range rejection.
- Required evidence: exact-head independent review, protected PR checks, and focused changed-area tests.
- Acceptance criteria:
  - Effective-context bytes reconstructed after compactions 1, 2, and 3 equal independent owner-side snapshots.
  - Each compaction retains a superset of all previously retained raw-fact identities.
  - Fresh durable replay and the live Session have zero compaction differential.
  - Corrupt context bytes, digest, ranges, indexes, or fact identities fail closed.
  - Internal compaction events never leak through the current public event revision or consume its visible limits.

## 6) Rollout Plan

- Rollout phases: merge through protected PR checks, rebuild the installed engine artifact from merged `main`, then exercise compaction in the final substrate journey.
- Flags/toggles: none.
- Blast radius constraints: Product Session replay and SessionState snapshot creation only.
- Monitoring hooks: replay differential, journal validation, and public event pagination lanes.

## 7) Rollback Plan

- Trigger conditions: replay divergence, any raw-fact loss, context digest mismatch, public event leakage, or protected checks failing.
- Exact rollback commands: revert the protected-main merge with `git revert -m 1 <merge-sha>`; do not force-push `main`.
- Artifact/state restoration steps: restore the preceding clean engine wheel. Sessions containing the new internal event kind require the matching reader until the journal is archived.
- Post-rollback verification: rerun Session replay, SessionState event, and public event pagination tests.

## 8) Approvals

- Kernel reviewer: independent exact-head correctness review required before merge.
- Contracts reviewer: compatibility is non-breaking because the new kind remains internal.
- Ops reviewer: protected danger-zone policy and security checks control merge.
- Final decision: protected branch checks and repository merge policy control merge.
