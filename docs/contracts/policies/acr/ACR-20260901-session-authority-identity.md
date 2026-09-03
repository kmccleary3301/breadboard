# ACR-20260901-session-authority-identity

- `acr_id`: `ACR-20260901-session-authority-identity`
- `title`: Bind durable session authority to directory identity
- `author`: engine campaign agent
- `date`: 2026-09-01
- `status`: implemented

## 1) Problem Statement

Internal sessions created against the configured default event root were not bound to the canonical workspace session store. Inferring that binding from resolved path equality alone would allow a writable workspace session-root symlink to match during creation and be replaced before terminal persistence, redirecting the authority grant.

## 2) Scope and Surfaces

- Kernel modules touched: session service orchestration, session runner terminal persistence, and product session storage.
- Extension modules touched: none.
- Contract surfaces touched: internal session creation and terminal durable projection authority.
- Is this a **kernel danger-zone** change? `yes`
- Danger-zone: yes.
- Outside scope: public schemas, provider behavior, RL, Slurm, evaluation, publication, and signing.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no.
- Cross-harness parity narrowed: no.
- Default endpoint semantics altered: no. Canonical sessions now retain their existing durable projection across restart.
- Coupling risk score: `medium`. The authority decision spans event-root selection, delayed terminal transition, and filesystem identity.

## 4) Change Classification

- Classification: `internal`
- Compatibility window: clean cutover. Existing callers of product session persistence remain source-compatible because directory-identity arguments are optional.
- Required schema/version bumps: none.

## 5) Evidence and Validation Plan

- Required contract lane tests: default-root internal authority, public durable session restart, and Python SDK default-server smoke.
- Required security tests: reject an initially symlinked session directory and reject replacement of the bound directory before terminal persistence.
- Required evidence: exact-head correctness review, exact-head security review, protected PR checks, and installed cross-client restart proof.
- Acceptance criteria:
  - Internal sessions using the configured canonical workspace event root reload after terminal transition.
  - Unrelated event roots remain unbound.
  - Session-root symlinks fail closed.
  - Terminal persistence verifies the captured device/inode on the directory descriptor used for projection writes.
  - Artifact-manifest authorization rechecks the same captured identity.

## 6) Rollout Plan

- Rollout phases: merge through protected PR checks, rebuild clean engine artifacts from merged `main`, repin the TUI to that protected identity, then rerun the installed cross-client restart journey.
- Flags/toggles: none.
- Blast radius constraints: durable session projection only; no package publication or signing.
- Monitoring hooks: default-server SDK smoke and installed CLI/HTTP/Python/TypeScript/TUI readback.

## 7) Rollback Plan

- Trigger conditions: canonical internal sessions fail creation or terminal projection, unrelated roots gain authority, or required protected checks fail.
- Exact rollback commands: revert the protected-main merge with `git revert -m 1 <merge-sha>`; do not force-push `main`.
- Artifact/state restoration steps: restore the preceding clean wheel, SDK tarball, engine distribution, and TUI provenance pin. No schema migration is required.
- Post-rollback verification: rerun public session restart, default-server SDK smoke, and the installed cross-client journey.

## 8) Approvals

- Kernel reviewer: independent exact-head correctness review required.
- Contracts reviewer: protected compatibility and contract gates required.
- Ops reviewer: independent exact-head security review required.
- Final decision: protected branch checks and repository merge policy control merge.
