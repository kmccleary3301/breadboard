# ACR-20260924-containment-qualification-receipt

- `acr_id`: `ACR-20260924-containment-qualification-receipt`
- `title`: Verified Containment Receipts in Qualification and Outer Isolation Deprecation
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-24
- `status`: implemented

## 1) Problem Statement

Headless requests formerly accepted `outer_isolation`; that declaration did not prove per-lease containment. This change rejects the obsolete field and verifies signed receipts at the public trusted-process admission gates:
1. `SandboxRuntimeManager.open` and `open_verifier` verify each launched lease's receipt against its lease ID, runtime ID, and installed authenticator before activation.
2. `ConductorAdapter.open` checks trusted-process receipts against the composer-injected authenticator, not the workspace's claimed signer, and refuses `UNCONFINED_TEST_ONLY` unless the composer explicitly enables a test-only lane.
3. `BreadBoardV2EpisodeService.create` verifies primary admission and `run` verifies verifier admission. A completed evidence root may be provisional during cleanup; CLOSED and a successful run response require verified primary and verifier teardown. Missing or invalid teardown quarantines, yields a failed run disposition, and cannot publish a closed reference.
4. The production pinned backend rejects an unconfined trusted-process plan; headless rejects unconfined trusted-process workspace inputs. These checks supplement, rather than replace, per-lease receipt verification.
5. Headless validation rejects `outer_isolation` with `ObsoleteOuterIsolationError`.

## 2) Scope and Surfaces

- Kernel modules touched: `breadboard/rl/harness/headless.py`, `breadboard/rl/harness/sandbox.py`, `breadboard/rl/harness/service.py`, `breadboard/rl/harness/runners/conductor.py`
- Test surfaces: `test_qualification_containment_receipt.py`, `test_sandbox_runtime.py`, `test_v2_service.py`, `test_runner_conductor.py`, and their deterministic fixtures.
- Contract surface: headless request validation and trusted-process lease admission/cleanup.
- Kernel danger-zone change? yes

## 3) Coupling and Generalization Impact

- Core-to-extension dependency: none; admission uses the existing typed receipt and manager ports.
- Cross-harness behavior: trusted-process sessions require a valid lease-bound receipt, including injected backends.
- Non-trusted-process runtimes remain subject to their existing isolation gates.
- Coupling risk: manager, service, and conductor must each independently reject counterfeit lease evidence; a backend's own claim is insufficient.

## 4) Change Classification

- Classification: `breaking`
- Compatibility window: `outer_isolation` is rejected with `ObsoleteOuterIsolationError`; callers must rely on verified containment attestation.
- Required schema/version bumps: none beyond existing versioned contracts.

## 5) Evidence and Validation Plan

- Regression tests exercise missing primary and verifier admission receipts, self-signed workspace counterfeits, unconfined conductor and headless entrypoints, signed teardown failure, and repeat-five composition descriptor stability.
- Headless tests reject obsolete `outer_isolation` in workspace and run requests.
- These local focused tests do not establish a Linux installed qualification or live containment claim; those require separate capture/replay and installed evidence.

## 6) Rollout Plan

- Rollout: review the rebased qualification branch, run the danger-zone ACR check and focused tests, then seek independent exact-head review.
- Flags/toggles: none in production admission.
- Blast radius: trusted-process runtime allocation, verifier execution and cleanup, conductor admission, and headless request validation.

## 7) Rollback Plan

- Trigger conditions: any failure in production composition qualification or regression in headless request processing.
- Exact rollback commands: revert commit with `git revert <commit-sha>`.
- Artifact/state restoration: revert the headless, manager, service, conductor, test, and ACR changes together.
- Post-rollback verification: rerun the focused admission and headless suites.

## 8) Approvals

- Kernel reviewer: pending independent exact-head review.
- Contracts reviewer: pending Main decision.
- Ops reviewer: pending installed qualification.
- Final decision: pending Main decision.
