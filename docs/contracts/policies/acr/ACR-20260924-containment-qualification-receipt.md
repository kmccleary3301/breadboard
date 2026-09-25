# ACR-20260924-containment-qualification-receipt

- `acr_id`: `ACR-20260924-containment-qualification-receipt`
- `title`: Verified Containment Receipts in Qualification and Outer Isolation Deprecation
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-24
- `status`: implemented

## 1) Problem Statement

Headless requests formerly accepted `outer_isolation`; that declaration did not prove per-lease containment. This change rejects the obsolete field and verifies signed receipts at the public trusted-process admission gates:
1. `SandboxRuntimeManager.open` and `open_verifier` verify each launched lease's receipt against its lease ID, runtime ID, and installed authenticator before activation. Successful admissions enter a manager-owned ledger; teardown removes the entry before cleanup awaits.
2. `ConductorAdapter.open` verifies the receipt with the composer-injected authenticator and requires an exact receipt match in the composer's read-only view of the live admission ledger. The workspace supplies neither that ledger nor the authenticator; `UNCONFINED_TEST_ONLY` remains refused unless the composer explicitly enables a test-only lane.
3. `BreadBoardV2EpisodeService.create` verifies primary admission and `run` verifies verifier admission. A completed evidence root may be provisional during cleanup; CLOSED and a successful run response require verified primary and verifier teardown. Missing or invalid teardown quarantines, yields a failed run disposition with a failure fact, and cannot publish a closed reference.
4. The production pinned backend rejects an unconfined trusted-process plan; headless rejects unconfined trusted-process workspace inputs. These checks supplement, rather than replace, per-lease receipt verification.
5. Headless validation rejects `outer_isolation` with `ObsoleteOuterIsolationError`.

## 2) Scope and Surfaces

- Kernel modules touched: `breadboard/rl/harness/headless.py`, `breadboard/rl/harness/lease_envelope.py`, `breadboard/rl/harness/sandbox.py`, `breadboard/rl/harness/service.py`, `breadboard/rl/harness/runners/conductor.py`, `breadboard/rl/harness/composition.py`, and `breadboard/rl/harness/history.py`.
- Test surfaces: `test_qualification_containment_receipt.py`, `test_sandbox_runtime.py`, `test_sandbox_process_integration.py`, `test_v2_service.py`, `test_runner_conductor.py`, `test_runner_policy_runtime.py`, `test_headless_runner.py`, and their deterministic fixtures.
- Contract surface: headless request validation, production composition and replay history, and trusted-process lease admission/cleanup.
- Kernel danger-zone change? yes

## 3) Coupling and Generalization Impact

- Core-to-extension dependency: none; admission uses the typed receipt and a lookup-only manager admission ledger port.
- Cross-harness behavior: trusted-process sessions require a valid lease-bound receipt, including injected backends.
- Non-trusted-process runtimes remain subject to their existing isolation gates.
- Trust boundary: a signed receipt alone does not establish manager admission. Composer injection supplies the independently populated, lookup-only lease ledger to the adapter; workspace-provided claims and a genuine signer without admission are insufficient. Python object introspection into private lease/manager internals is outside this threat model; secrecy of the HMAC signer is not the admission boundary.
- Receipt boundary: mappings and receipt objects are copied through one bounded, exact-type plain-JSON walker before schema key checks, signature verification, or admission ledger comparison. Invalid keys, subclasses, hostile objects, and excessive nesting produce `containment_receipt_invalid`; exceptions outside `Exception` propagate.

## 4) Change Classification

- Classification: `breaking`
- Compatibility window: `outer_isolation` is rejected with `ObsoleteOuterIsolationError`; callers must rely on verified containment attestation.
- Required schema/version bumps: none beyond existing versioned contracts.

## 5) Evidence and Validation Plan

- Regression tests exercise missing primary and verifier admission receipts, a genuine composer-signed counterfeit without admission, exact-match modification and post-teardown replay, unconfined conductor and headless entrypoints, a 36-case cleanup/teardown receipt matrix, and repeat-five composition descriptor stability.
- Receipt regressions cover schema-derived nested subclass and bool/int mutations, forged string keys, hostile outcome and mapping objects, excessive nesting, and genuine mapping/object admission controls.
- Headless tests reject obsolete `outer_isolation` in workspace and run requests.
- These local focused tests do not establish a Linux installed qualification or live containment claim; those require separate capture/replay and installed evidence.

## 6) Rollout Plan

- Rollout: review the rebased qualification branch, run the danger-zone ACR check and focused tests, then seek independent exact-head review.
- Flags/toggles: none in production admission.
- Blast radius: trusted-process runtime allocation, verifier execution and cleanup, conductor admission, production composition/history replay, and headless request validation.

## 7) Rollback Plan

- Trigger conditions: any failure in production composition qualification, lease admission/replay, or headless request processing.
- Exact rollback commands: revert the qualification fix and preceding containment commits in reverse order with `git revert <qualification-fix-sha> <preceding-containment-sha>`, using the deployed commit IDs.
- Artifact/state restoration: revert headless, lease envelope, manager, service, conductor, composition, history, tests, and ACR changes together.
- Post-rollback verification: rerun the focused admission, composition, replay, and headless suites.

## 8) Approvals

- Kernel reviewer: pending independent exact-head review.
- Contracts reviewer: pending Main decision.
- Ops reviewer: pending installed qualification.
- Final decision: pending Main decision.
