# ACR-20260831-public-interface-parity

- `acr_id`: `ACR-20260831-public-interface-parity`
- `title`: Complete installed public interface parity and canonical compiler ownership
- `author`: engine campaign agent
- `date`: 2026-08-31
- `status`: implemented

## 1) Problem Statement

The accepted I6 public-interface work and J1 compiler-ownership cutover touch product CLI, public SDK, generated bindings, public schemas, and compiler modules. CI requires an architecture-change record on any PR that edits kernel danger-zone globs. Without this record the already-accepted public surface cannot merge.

## 2) Scope and Surfaces

- Kernel modules touched: `breadboard/product/cli/**`, `breadboard/product/operations/**`, `breadboard_sdk/**`, `breadboard_engine/compilation/**`, `breadboard_engine/api/public/**`.
- Extension modules touched: none.
- Contract surfaces touched: `event`, public operation catalog, public session/lifecycle schemas, generated client bindings.
- Is this a **kernel danger-zone** change? `yes`
- Danger-zone: yes.
- Outside scope: J2 live E4 evidence-owner internalization, TUI identity packets, sandbox/SWE remaining proof, package freeze, and release publication.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no.
- Cross-harness parity narrowed: no.
- Default endpoint semantics altered: no new product operations; existing 26 public operations now bind complete request/result types and session event streams.
- Coupling risk score: `medium`. Generated Python/TypeScript/TUI skeleton bindings must stay in lockstep with `contracts/public/operations.v2.json`.

## 4) Change Classification

- Classification: `additive`
- Compatibility window: public operation count remains 26; clients gain named public result/event types instead of a wildcard type re-export.
- Required schema/version bumps: public session event and product session lifecycle schemas are added; catalog id remains `bb.public_operation_catalog.v2`.

## 5) Evidence and Validation Plan

- Required contract lane tests: `tests/test_public_binding_codegen.py`, `tests/test_public_binding_parity.py`, `tests/test_breadboard_cli_packaging.py`.
- Required replay/parity checks: none; this PR does not change E4 replay bytes.
- Required conformance/ablation checks: existing CI product-spine, compilation, and public-binding gates.
- Required evidence bundles to refresh: none beyond PR CI receipts.
- Acceptance criteria:
  - TypeScript root exports match the named public surface with no `./types.js` wildcard.
  - Installed-wheel public probe reports 26 operations and 27 schemas.
  - Canonical compiler production path does not import `scripts.e4_parity`.
  - Exact-head independent review accepts the published head before merge.

## 6) Rollout Plan

- Rollout phases: merge this PR onto protected `main` after CI and exact-head review; keep J2 stacked and draft until remaining adapter-root findings are repaired.
- Flags/toggles: none.
- Blast radius constraints: public clients that imported `ROUTES` or wildcard types from `@breadboard/sdk` must use the named exports.
- Monitoring hooks: product-spine packaging probe and public-binding parity tests.

## 7) Rollback Plan

- Trigger conditions: installed public clients fail to compile or public operation count drifts from 26.
- Exact rollback commands: revert this PR; restore the previous public SDK export list.
- Artifact/state restoration steps: regenerate public bindings from the reverted catalog if needed.
- Post-rollback verification: rerun product-spine public-binding and packaging tests.

## 8) Approvals

- Kernel reviewer: required on the exact PR head.
- Contracts reviewer: required on the exact PR head.
- Ops reviewer: not required.
- Final decision: owner approval required after CI and independent review; this ACR does not authorize merge.
