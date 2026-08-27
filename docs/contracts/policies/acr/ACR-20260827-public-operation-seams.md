# ACR-20260827-public-operation-seams

- `acr_id`: `ACR-20260827-public-operation-seams`
- `title`: Typed provider-neutral public operation seams and generated bindings
- `author`: Codex (engine PR handoff)
- `date`: 2026-08-27
- `status`: implemented

## 1) Problem Statement

Product, TUI, and RL consumers need one typed provider-neutral public surface. The accepted implementation migrates product operations away from CLI/provider internals, generates catalog-owned Python and TypeScript bindings, and corrects the TypeScript session-get envelope. Those changes touch product and contract danger-zone paths and require an explicit architecture decision.

## 2) Scope and Surfaces

- Typed system description plus read-only and mutating operation seams.
- Catalog-owned public bindings, Python and TypeScript client types, OpenAPI/reference documentation, and fixed-point generators.
- Provider-free public integration registration and session-get envelope alignment.
- Danger-zone: yes.
- Outside scope: hidden operational runtime-setup routes, D7 TUI source edits, RL amendment implementation, release publication, signing, and notarization.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no.
- Provider-specific public coupling introduced: no; operations terminate at provider-neutral seams.
- Default endpoint semantics altered: additive operations plus a session-get envelope correction.
- Generalization impact: positive; one catalog owns operation IDs, routes, types, bindings, and reference docs.
- Coupling risk: medium because generator, runtime, and consumer surfaces must remain synchronized.

## 4) Change Classification

- Classification: `additive`.
- Compat reviewed: non-breaking.
- Compatibility window: public routes are additive; the TypeScript session-get reader is corrected to match the canonical Python/HTTP envelope. Internal CLI/provider paths are not compatibility targets.
- Source provenance: exact original patches are replayed onto the ENG-02 ACR-bearing base; author identity/date and stable patch IDs remain recorded.

## 5) Evidence and Validation Plan

- Canonical manifests: I1 `b5440b1400fc21593c6208135abf7cb15e5392e3d339e2f592c51bac684c8c2d`; I2 `43e8c66eadc16017670b95ef92b9aa5d1c4d4ad101da0526fe1f5ea4d750d569`; I3 `7f5d69fa609dc933f5b6e7d23b61d0f693d4575744a8f0cd44d97f19281d56f3`; I5 correction `b9d9b3414fb2c842145e699acc00d120870c09974352da371bc99d554a0b2a21`.
- Required gates: public binding generator fixed point, surface inventory/OpenAPI parity, Python and TypeScript SDK tests, deterministic clean-consumer pack/install, provider-free operation smoke, danger-zone ACR guard, and fresh exact-head spec/architecture review.
- Acceptance criteria:
  - the 26-operation catalog, routes, generated bindings, OpenAPI, and reference docs agree;
  - provider-free integrations require no provider credential;
  - Python and TypeScript session-get envelopes agree;
  - generated file modes remain stable.

## 6) Rollout Plan

1. Merge only after ENG-01 and ENG-02 and after required CI/review pass.
2. Re-ground on each merged predecessor without altering the runtime patches.
3. Freeze runtime/package inputs only after this and all accepted RL runtime PRs merge or are explicitly deferred.
4. Build SDK and engine distributions from the final clean frozen main.

No release, signing, notarization, or direct `main` push is authorized by this record.

## 7) Rollback Plan

- Revert this PR independently before reverting ENG-02 or ENG-01.
- Re-run generator fixed points, public contract parity, provider-free smoke, and clean-consumer install after rollback.
- Do not reintroduce provider-specific public coupling as a fallback.

## 8) Approvals

- Kernel/contracts reviewer: required on the exact PR head.
- SDK consumer reviewer: required on the exact PR head.
- Final decision: owner approval required after CI and independent review; this ACR does not authorize merge.
