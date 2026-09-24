# ACR-20260924-containment-qualification-receipt

- `acr_id`: `ACR-20260924-containment-qualification-receipt`
- `title`: Verified Containment Receipts in Qualification and Outer Isolation Deprecation
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-24
- `status`: implemented

## 1) Problem Statement

Qualification previously permitted `TRUSTED_PROCESS` runtime execution without validating a per-lease `ContainmentReceipt`. That left our safest execution lane's containment attestation unverified during qualification runs. In addition, callers could declare the obsolete `outer_isolation` parameter in headless requests instead of relying on per-lease verified containment attestation.

To fail closed and ensure end-to-end security provenance:
1. Caller-declared `outer_isolation` in `HeadlessWorkspaceInput` and `HeadlessRunRequest` is rejected with a typed `ObsoleteOuterIsolationError` and dropped from request identity.
 2. For `TRUSTED_PROCESS`, qualification verifies `ContainmentReceipt` using `verify_containment_receipt` with the receipt authenticator, failing closed on missing, tampered, or lease-mismatched receipts.

 ## 2) Scope and Surfaces
 
 - Kernel modules touched:
   - `breadboard/rl/harness/headless.py`
   - `breadboard/rl/harness/qualification.py`
 - Test files touched:
   - `tests/rl/harness/test_qualification_containment_receipt.py`
 - Contract surfaces touched:
   - Headless request schema validation and identity dictionary
   - Qualification containment verification API and fixture
 - Kernel danger-zone change? yes
## 3) Coupling and Generalization Impact

- Does this add any core -> extension dependency? no. Qualification and headless execution use existing typed primitives in `lease_envelope` and `composition`.
- Does this narrow cross-harness parity behavior? yes. Trusted process execution requires valid containment attestation.
- Does this alter default endpoint semantics? no. Non-trusted-process runtimes (such as Docker) are unaffected.
- Coupling risk score (`low`) and rationale: the change enforces already-specified containment receipt verification at the qualification boundary and explicitly removes an obsolete headless field.

## 4) Change Classification

- Classification: `breaking`
- Compatibility window: `outer_isolation` is rejected with `ObsoleteOuterIsolationError`; callers must rely on verified containment attestation.
- Required schema/version bumps: none beyond existing versioned contracts.

## 5) Evidence and Validation Plan

- Required contract lane tests:
  - `tests/rl/harness/test_qualification_containment_receipt.py` (accepts signed receipt, rejects missing receipt, rejects tampered signature, rejects lease mismatch, rejects obsolete outer_isolation).
  - Headless runner tests (`tests/rl/harness/test_headless_runner.py`).
  - Production composition lifecycle tests (`tests/rl/harness/test_production_composition_public_lifecycle.py`).
  - Qualification fixture generator tests (`tests/rl/harness/test_production_composition_fixture_generator.py`).
- Pre-fix proof: tests failed at `66cade05` due to missing `verify_qualification_containment` and `ObsoleteOuterIsolationError`.
- Post-fix verification: all qualification containment tests pass (8 passed).

## 6) Rollout Plan

- Rollout phases: merge to branch `e4/containment-qual-20260924`, validate danger-zone ACR check and focused test suite.
- Flags/toggles: none; verification is mandatory for `TRUSTED_PROCESS`.
- Blast radius constraints: limited to trusted-process qualification and headless request validation.

## 7) Rollback Plan

- Trigger conditions: any failure in production composition qualification or regression in headless request processing.
- Exact rollback commands: revert commit with `git revert <commit-sha>`.
- Artifact/state restoration steps: restore previous headless and qualification modules.
- Post-rollback verification: rerun qualification and headless test suites.

## 8) Approvals

- Kernel reviewer: Main (campaign orchestrator) under Kyle McCleary's standing approval
- Contracts reviewer: Main (campaign orchestrator) under Kyle McCleary's standing approval
- Ops reviewer: Main (campaign orchestrator) under Kyle McCleary's standing approval
- Final decision: Main (campaign orchestrator) under Kyle McCleary's standing approval
