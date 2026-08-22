# ACR-20260708-rl-phase3-promotion-and-guardrails

- `acr_id`: `ACR-20260708-rl-phase3-promotion-and-guardrails`
- `title`: RL Phase 3 promotion, evidence gates, and danger-zone guard enforcement
- `author`: codex
- `date`: 2026-07-08
- `status`: implemented

## 1) Problem Statement

PR #27 adds RL Phase 3 runtime, target-evidence, API, promotion-audit, and CLI bridge surfaces. Those changes touch kernel danger-zone paths and promotion-critical evidence validators.

The current branch needs explicit architecture review coverage because a green CI run is not enough for these surfaces:

- final-report promotion must be tied to content-addressed evidence, not path existence;
- P3-M11 observability/object-store/scheduler claims must require real non-local tokened proof;
- public RL run lifecycle APIs must reject invalid resources and invalid terminal-state transitions;
- danger-zone CI guards must fail closed instead of skipping when guard scripts are missing.

If we do nothing, the PR can claim `1000/1000` readiness while validating self-consistency rather than artifact truth, and future danger-zone PRs can pass without the documented ACR gate.

## 2) Scope and Surfaces

- Kernel modules touched: `breadboard/rl/phase3/**`, `breadboard/rl/phase2/service.py` contract consumers, `agentic_coder_prototype/api/cli_bridge/app.py` route exposure.
- Extension modules touched: none directly.
- Contract surfaces touched: `event`, `artifact`, `provider-request`, `promotion-audit`, `operator-script`, `danger-zone-governance`.
- Is this a **kernel danger-zone** change? `yes`
- Danger-zone: yes.

## 3) Coupling and Generalization Impact

- Core -> extension dependency introduced: no.
- Does this add any core -> extension dependency? `no`
- Does this narrow cross-harness parity behavior? `no`
- Does this alter default endpoint semantics? `yes`; it exposes `/rl/*` route projections and tightens accepted live-run lifecycle semantics.
- Coupling risk score: `medium`.

Rationale: the branch adds a new RL subsystem and public API projection, but the patch keeps the kernel boundary explicit by validating evidence, preserving scorecard-update controls, and failing danger-zone governance closed.

## 4) Change Classification

- Classification: `additive`
- Compatibility window: current PR only; no stable external RL API version has shipped from this branch.
- Required schema/version bumps: none for existing kernel contract pack v1; RL Phase 3 report schemas remain v1 and become stricter about existing fields.

## 5) Evidence and Validation Plan

- Required contract lane tests: focused RL API/router, live service store, evidence gates, final report builder, and governance script checks.
- Required replay/parity checks: existing PR CI replay/conformance jobs remain required; no replay fixture format is changed by this ACR.
- Required conformance/ablation checks: kernel contract pack hash check and danger-zone ACR check must run instead of skipping.
- Required evidence bundles to refresh: Phase 3 final report artifacts should be regenerated only through existing `scripts/rl_phase3/*` commands when canonical evidence changes; this patch does not hand-edit `docs_tmp` evidence.
- Acceptance criteria:
  - stale artifact input hashes fail validation;
  - P3-M11 missing tokened object-store round-trip proof fails validation;
  - active/core readiness point totals derive from milestone points;
  - invalid RL run resources and terminal-state transitions are rejected;
  - danger-zone guard scripts exist, hash listed contract-pack files, and require a changed ACR artifact for protected path diffs.

## 6) Rollout Plan

- Rollout phases:
  1. Land stricter validators and regression tests on PR #27.
  2. Keep existing `scorecard_update_allowed` false inside generated promotion-review artifacts unless the scorecard promotion path explicitly updates the scorecard artifact.
  3. Let CI enforce the restored danger-zone scripts on subsequent protected-path changes.
- Flags/toggles: none.
- Blast radius constraints: local to PR #27 branch; no production service migration or target credential rotation.
- Monitoring hooks: GitHub checks for Python, conformance, replay determinism, contract governance, and danger-zone guard.

## 7) Rollback Plan

- Trigger conditions:
  - contract gate failure;
  - replay determinism regression;
  - boundary/coupling violation;
  - sustained operational instability from stricter RL API validation.
- Exact rollback commands:
  - `git revert <commit>` for the PR #27 cleanup commit if the stricter validators or CI guards block unexpectedly.
  - If only the governance checker misclassifies a safe PR, revert the checker change and keep the runtime validation patch.
- Artifact/state restoration steps:
  - Do not mutate canonical `docs_tmp` evidence during rollback.
  - Re-run the existing Phase 3 report builders before any future evidence promotion.
- Post-rollback verification:
  - run focused RL tests touched by this ACR;
  - run `python3 scripts/check_kernel_contract_pack_v1.py --manifest docs/contracts/policies/kernel_contract_pack_v1_manifest.json --repo-root .`;
  - run `python3 scripts/check_danger_zone_acr.py --manifest docs/contracts/policies/kernel_danger_zone_manifest_v1.json --changed-files-file <changed-files>`.

## 8) Approvals

- Kernel reviewer: PR #27 reviewer gate.
- Contracts reviewer: PR #27 reviewer gate.
- Ops reviewer: PR #27 reviewer gate.
- Final decision: implemented for PR #27 cleanup commit; merge remains subject to normal PR review and CI.
