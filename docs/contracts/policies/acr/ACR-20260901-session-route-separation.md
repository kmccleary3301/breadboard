# ACR-20260901-session-route-separation

- `acr_id`: `ACR-20260901-session-route-separation`
- `title`: Separate public and canonical internal session routes
- `author`: engine campaign agent
- `date`: 2026-09-01
- `status`: implemented

## 1) Problem Statement

The default server assigned `/v1/sessions` to the public projected API after removing the raw canonical routes. The TUI canonical SDK expected raw session envelopes at the same path, so it reported `session_contract_missing` unless the launcher enabled legacy routes. One default server must support public clients and the TUI against one `SessionService` without weakening public redaction.

## 2) Scope and Surfaces

- Kernel modules touched: CLI bridge route registration, engine identity, lifecycle models, and the compatibility SDK client.
- Extension modules touched: none.
- Contract surfaces touched: public session HTTP, canonical internal session HTTP, P30 engine identity, Python SDK, and TypeScript SDK.
- Is this a **kernel danger-zone** change? `yes`
- Danger-zone: yes.
- Outside scope: RL, Slurm, evaluation, package publication, and signing.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no.
- Cross-harness parity narrowed: no.
- Default endpoint semantics altered: the public `/v1/sessions` surface keeps projected envelopes; canonical raw routes move to `/v1/internal/sessions` and remain on the default server.
- Coupling risk score: `medium`. The engine identity digest and internal SDK paths must change together. The public operation catalog and public OpenAPI must remain unchanged except for the reviewed P30 digest literals.

## 4) Change Classification

- Classification: `internal`
- Compatibility window: clean cutover before publication. Canonical internal callers move to `/v1/internal/sessions`; public callers remain on `/v1/sessions`.
- Required schema/version bumps: P30 session schema digest changes to `sha256:979bff06137b659c0110c0f9324703b955e22da85a7aac93bee7f639290475a9`. Protocol and package versions remain unchanged.

## 5) Evidence and Validation Plan

- Required contract lane tests: engine identity, lifecycle authority, CLI bridge export, TypeScript SDK codegen, Python SDK, E4 API surface, public session API, and the default-profile route error regression.
- Required replay/parity checks: product compilation and replay determinism on the exact PR head.
- Required conformance/ablation checks: public binding parity, clean SDK consumer, wheel packaging, and default-server SDK smokes.
- Required evidence: exact-head correctness review, exact-head security review, patch secret scan, and protected PR checks.
- Acceptance criteria:
  - Default engine identity reports the P30 contract compatible and ready.
  - Public and internal routes share one session registry and session identifier.
  - Public OpenAPI excludes `/v1/internal` paths.
  - Python and TypeScript SDK hello scripts complete against the same default server.
  - The Python and TypeScript public SDK hello clients reject bearer use on plaintext non-loopback requests; the engine execution child allowlist excludes bearer and provider credentials.

## 6) Rollout Plan

- Rollout phases: merge engine PR #91 after required checks, build clean wheel and SDK artifacts from merged `main`, update the TUI artifact pin, then merge the TUI PR.
- Flags/toggles: none. TUI local-owned launch removes `BREADBOARD_LEGACY_ROUTES=1`.
- Blast radius constraints: route and SDK changes only; no release publication.
- Monitoring hooks: engine identity readiness, public binding parity, SDK default-server smoke, and TUI lifecycle integration.

## 7) Rollback Plan

- Trigger conditions: P30 readiness fails, public route projections change, internal SDK calls return public envelopes, or required PR checks fail.
- Exact rollback commands: revert the protected-main merge with `git revert -m 1 <merge-sha>`; do not force-push `main`.
- Artifact/state restoration steps: restore the preceding clean wheel, canonical SDK tarball, TUI provenance manifest, and lockfile. Session state uses the same `SessionService` format and needs no migration.
- Post-rollback verification: rerun engine identity, public binding parity, SDK default-server smoke, wheel packaging, and TUI lifecycle tests.

## 8) Approvals

- Kernel reviewer: independent review required on the final exact head; code head `926a20b27223051f4b2b4b84e1c007d4dcd6e289` accepted.
- Contracts reviewer: independent review required on the final exact head.
- Ops reviewer: independent security review required on the final exact head; code head `926a20b27223051f4b2b4b84e1c007d4dcd6e289` accepted.
- Final decision: protected branch checks and repository merge policy control merge.
