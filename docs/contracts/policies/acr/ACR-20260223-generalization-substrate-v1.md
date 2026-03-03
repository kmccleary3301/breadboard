# ACR-20260223-generalization-substrate-v1

- `acr_id`: `ACR-20260223-generalization-substrate-v1`
- `title`: Generalization substrate hardening (danger-zone guards + evidence/conformance primitives)
- `author`: codex
- `date`: 2026-02-23
- `status`: implemented

## 1) Problem Statement

Kernel and conformance-critical surfaces lacked enforceable guardrails tying danger-zone edits to explicit architecture review artifacts.

## 2) Scope and Surfaces

- Kernel modules touched: `breadboard/**`, selected guard scripts.
- Contract surfaces touched: event/tool/artifact/conformance guardrails.
- Kernel danger-zone: yes.

## 3) Coupling and Generalization Impact

- Core -> extension dependency introduced: no.
- Default endpoint semantics changed: no.
- Cross-harness narrowing introduced: no.
- Coupling risk score: medium (guardrail and CI behavior changes).

## 4) Change Classification

- Classification: additive.
- Compatibility: no runtime API break expected.
- Schema/version bumps: additive v1 artifacts only.

## 5) Evidence and Validation Plan

- Required tests: conformance, replay determinism gate, boundary guard, evidence bundle tests.
- Acceptance: all targeted tests pass and CI guards enforce danger-zone policy.

## 6) Rollout Plan

- Rollout via CI job additions (`danger_zone_acr_guard`, wave-A, boundary guard).
- No feature flags required.

## 7) Rollback Plan

- Revert guard jobs and associated scripts/docs if false positives block delivery.
- Re-run baseline CI suite after rollback.

## 8) Approvals

- Kernel reviewer: pending.
- Contracts reviewer: pending.
- Ops reviewer: pending.
