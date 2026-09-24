# ACR-20260924-containment-qualification-receipt

- `acr_id`: `ACR-20260924-containment-qualification-receipt`
- `title`: Verified Containment Receipts in Qualification and Outer Isolation Deprecation
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-24
- `status`: implemented

## 1) Problem Statement

Callers could previously declare the obsolete `outer_isolation` parameter in headless requests instead of relying on per-lease verified containment attestation.
In addition, trusted-process containment requires end-to-end receipt verification:
1. `service.py:1490-1514` enforces per-lease `ContainmentReceipt` verification via `verify_containment_receipt` at allocation time when runtime is `TRUSTED_PROCESS` and containment is `ATTESTED`, failing closed on missing, tampered, or mismatched receipts.
2. `_PinnedTrustedProcessBackend.launch` strictly enforces `ATTESTED` containment and rejects `UNCONFINED_TEST_ONLY` with `SandboxLaunchError("production composition rejects unconfined trusted-process execution")`.
3. `headless.py:527-531` rejects trusted-process execution with `containment != "attested"`.
4. Caller-declared `outer_isolation` in `HeadlessWorkspaceInput` and `HeadlessRunRequest` is rejected with a typed `ObsoleteOuterIsolationError` using concise `@model_validator(mode="before")` hooks and dropped from request identity.

## 2) Scope and Surfaces

- Kernel modules touched:
  - `breadboard/rl/harness/headless.py`
  - `breadboard/rl/harness/qualification.py`
- Test files touched:
  - `tests/rl/harness/test_qualification_containment_receipt.py`
- Contract surfaces touched:
  - Headless request schema validation and identity dictionary
- Kernel danger-zone change? yes

## 3) Coupling and Generalization Impact

- Does this add any core -> extension dependency? no. Qualification and headless execution use existing typed primitives in `lease_envelope` and `composition`.
- Does this narrow cross-harness parity behavior? yes. Trusted process execution requires valid containment attestation.
- Does this alter default endpoint semantics? no. Non-trusted-process runtimes (such as Docker) are unaffected.
- Coupling risk score (`low`) and rationale: the change enforces already-specified containment receipt verification and cleanly removes an obsolete headless field with concise validation.

## 4) Change Classification

- Classification: `breaking`
- Compatibility window: `outer_isolation` is rejected with `ObsoleteOuterIsolationError`; callers must rely on verified containment attestation.
- Required schema/version bumps: none beyond existing versioned contracts.

## 5) Evidence and Validation Plan

- Required contract lane tests:
  - `tests/rl/harness/test_qualification_containment_receipt.py` (rejects obsolete outer_isolation on HeadlessWorkspaceInput and HeadlessRunRequest, verifies public qualification entry rejects unconfined trusted-process, verifies default containment is attested).
  - Headless runner tests (`tests/rl/harness/test_headless_runner.py`).
  - Production composition lifecycle tests (`tests/rl/harness/test_production_composition_public_lifecycle.py`).
  - Qualification fixture generator tests (`tests/rl/harness/test_production_composition_fixture_generator.py`).
- Pre-fix proof: at `66cade05`, `headless.py` accepted `outer_isolation: Literal['apptainer'] | None = None` and lacked `ObsoleteOuterIsolationError`.
- Post-fix verification: all focused tests pass.

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
