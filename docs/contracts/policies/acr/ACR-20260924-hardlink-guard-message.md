# ACR-20260924-hardlink-guard-message

- `acr_id`: `ACR-20260924-hardlink-guard-message`
- `title`: Make installed-module hardlink rejection actionable
- `author`: BreadBoard E4 implementation wave 1
- `date`: 2026-09-24
- `status`: implemented

## 1) Problem Statement

`measure_module_artifact` already rejects module files with more than one hardlink, protecting the runner identity from a cache that can be modified through another link. Its error only said that the artifact was not a private regular file, so an installed operator could not identify the hardlinked install or the supported repair. The required outcome is a fail-closed error naming the hardlinked installed module, normalized path, observed link count, and copy-mode reinstall remedy. Without this change, Linux `uv` installs can fail with an opaque diagnostic and operators may weaken the guard instead of reinstalling safely.

## 2) Scope and Surfaces

- Kernel module touched: `breadboard/rl/harness/runner_identity.py` (`measure_module_artifact`).
- Extension modules touched: none.
- Contract surfaces touched: runtime error text for an existing fail-closed identity check; no event, tool, artifact, or provider schema changes.
- Is this a **kernel danger-zone** change? `yes`.
- The issue decision recorded in `docs_tmp/wayfinder/e4-campaign-local-production/issues/25-installed-module-identity-hardlink.md` keeps rejecting hardlinks and adds no read-only-store exception. The supported install contract remains copy mode (`uv pip install --link-mode=copy`) or pip.

## 3) Coupling and Generalization Impact
- Danger-zone: yes.
- Does this add any core -> extension dependency? `no`.
- Does this narrow cross-harness parity behavior? `no`; the existing identity invariant remains unchanged.
- Does this alter default endpoint semantics? `no`.
- Coupling risk: `low`; only the diagnostic branch changes, while the guard, digest measurement, and identity comparison remain unchanged. Installed wheel construction in `conformance/provider_differential/artifact_rows.py` explicitly selects uv copy mode.

## 4) Change Classification

- Classification: `behavioral-change` (diagnostic text only; rejection behavior is unchanged).
- Compatibility window: existing callers continue to receive `RuntimeError`; consumers matching the old opaque message should use the stable cause phrase instead.
- Required schema/version bumps: none.

## 5) Evidence and Validation Plan

- Required contract lane tests: focused `tests/rl/harness/test_runner_identity.py` hardlinks a real temporary file and asserts the cause, path, nlink, and reinstall remedy.
- Required replay/parity checks: none; no accepted trace or schema changes.
- Required conformance/ablation checks: run `scripts/check_danger_zone_acr.py` against the changed-file list; the focused test must fail at the pre-fix head because the old message omits the required fields, then pass after the fix.
- Required evidence bundles to refresh: none.
- Acceptance criteria: hardlinked installed modules remain rejected; the error includes `hardlinked installed module`, `path=`, `nlink=`, and `uv pip install --link-mode=copy` or pip; installed BreadBoard wheel recipes use copy mode.

## 6) Rollout Plan

1. Review the implementation and focused real-hardlink regression test on the exact candidate head.
2. Run the danger-zone ACR check and the focused runner-identity test.
3. Use copy-mode installation for installed differential/handoff builds; no read-only-store exception is enabled.

- Flags/toggles: none.
- Blast radius constraints: only module identity measurement diagnostics change; hardlink rejection remains fail closed.
- Monitoring hooks: operator logs capture the path and observed nlink in the raised error.

## 7) Rollback Plan

- Trigger conditions: a supported single-link install is rejected, a caller cannot handle the diagnostic, or the focused identity test regresses.
- Exact rollback commands: revert the commit containing `breadboard/rl/harness/runner_identity.py`, `conformance/provider_differential/artifact_rows.py`, the focused test, this ACR, and the Issue 25 decision note as one reviewed change; do not relax the nlink guard independently.
- Artifact/state restoration steps: discard candidate installed outputs and rebuild from the prior approved source identity using the prior recipe; preserve the existing hardlink guard.
- Post-rollback verification: rerun the focused identity suite and the danger-zone ACR check on the reviewed rollback head, then re-establish copy-mode install evidence before accepting an installed handoff.

## 8) Approvals

- Kernel reviewer: required on the exact candidate head.
- Contracts reviewer: required on the exact candidate head.
- Ops reviewer: required for installed-handoff recipe verification.
- Final decision: Main's 2026-09-24 decision is to retain conservative hardlink rejection with no read-only-store exception; this ACR does not authorize merge by itself.
