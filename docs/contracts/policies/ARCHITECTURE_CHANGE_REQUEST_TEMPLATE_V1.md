# Architecture Change Request (ACR) Template V1

Use this template for any change that can alter kernel semantics, contract surfaces, replay determinism, or extension coupling.

## Header

- `acr_id`: `ACR-YYYYMMDD-<slug>`
- `title`:
- `author`:
- `date`:
- `status`: `draft | review | approved | rejected | implemented`

## 1) Problem Statement

- What current behavior is insufficient?
- What measurable outcome is required?
- What happens if we do nothing?

## 2) Scope and Surfaces

- Kernel modules touched:
- Extension modules touched:
- Contract surfaces touched (`event`, `tool`, `artifact`, `provider-request`, other):
- Is this a **kernel danger-zone** change? `yes|no`

## 3) Coupling and Generalization Impact

- Does this add any core -> extension dependency? `yes|no`
- Does this narrow cross-harness parity behavior? `yes|no`
- Does this alter default endpoint semantics? `yes|no`
- Coupling risk score (`low|medium|high`) and rationale:

## 4) Change Classification

- Classification: `additive | behavioral-change | breaking`
- Compatibility window:
- Required schema/version bumps:

## 5) Evidence and Validation Plan

- Required contract lane tests:
- Required replay/parity checks:
- Required conformance/ablation checks:
- Required evidence bundles to refresh:
- Acceptance criteria:

## 6) Rollout Plan

- Rollout phases:
- Flags/toggles:
- Blast radius constraints:
- Monitoring hooks:

## 7) Rollback Plan

- Trigger conditions:
- Exact rollback commands:
- Artifact/state restoration steps:
- Post-rollback verification:

## 8) Approvals

- Kernel reviewer:
- Contracts reviewer:
- Ops reviewer:
- Final decision:
