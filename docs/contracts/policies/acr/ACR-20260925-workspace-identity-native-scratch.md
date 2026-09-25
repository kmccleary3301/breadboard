# ACR-20260925-workspace-identity-native-scratch

- `acr_id`: `ACR-20260925-workspace-identity-native-scratch`
- `title`: Attested trusted-process native scratch adoption and descriptor identity verification
- `author`: BreadBoard E4 containment lane
- `date`: 2026-09-25
- `status`: implemented; independent exact-head review pending

## 1) Problem Statement

Following PR #144 (which resolved envelope launch EROFS on `/tmp`), trusted process envelope runs inside Apptainer SIFs encountered `WorkspaceStateError` with `code="workspace_authority_mismatch"` at turn 0:
1. In attested containment mode (`RuntimeContainment.ATTESTED`), `TrustedProcessBackend.launch` creates the native scratch directory (`scratch = context.native_scratch_path`).
2. During the subsequent native phase invocation (`invoke_native_phase`), `_create_native_scratch` attempted an unconditioned `os.mkdir(name, mode=0o700, dir_fd=root_fd)`. Because the scratch directory was already created at launch, `os.mkdir` raised `FileExistsError`, which was caught and re-raised as `WorkspaceStateError('native scratch authority is unavailable', code='workspace_authority_mismatch')`.
3. Additionally, descriptor identity checks in `TrustedProcessHandle._start_stopped_process` require that the workspace descriptor identity (`os.fstat(self._workspace_fd)`) strictly matches `self._workspace_identity` captured at materialization.

## 2) Scope and Surfaces

- `breadboard.rl.harness.sandbox`: `_create_native_scratch` adoption and validation, `TrustedProcessBackend.launch` strict scratch creation.
- `tests/rl/harness/test_sandbox_runtime`: regression tests for scratch directory adoption and rejection of invalid preexisting entries.
- `tests/rl/harness/test_sandbox_process_integration`: regression tests for workspace descriptor identity verification.
- Danger-zone: yes. Protected kernel/generalization surface. No weakening of identity or authority checks; checks remain fail-closed.

## 3) Coupling and Generalization Impact

`_create_native_scratch` now allows adopting an existing native scratch directory, but strictly verifies its authority:
- The path must be a directory (`stat.S_ISDIR`).
- The directory must be owned by the running effective UID (`st_uid == os.geteuid()`).
- The directory permissions must be strictly `0700` (`stat.S_IMODE == 0o700`).
- The directory must reside on the same filesystem/device as the lease root (`st_dev == root_device`).
- The directory name must match the expected lease scratch name (`_native_scratch_name(lease_id)`).
If any validation fails, the function fails closed with `WorkspaceStateError(code="workspace_authority_mismatch")`.
`TrustedProcessBackend.launch` strictly creates the scratch directory without `exist_ok=True`.

## 4) Change Classification

- Classification: `additive`.

Fail-closed, corrective change. No permissions or identity boundaries are loosened; authority checks are preserved and strictly verified across both creation and adoption.

## 5) Evidence and Validation Plan

- Unit test `test_create_native_scratch_adopts_existing_valid_directory` verifies that a valid existing directory with mode `0700` and matching UID/device is adopted and cleaned up cleanly.
- Unit test `test_create_native_scratch_rejects_existing_invalid_directory` verifies that invalid permissions (`0777`) or non-directory entries are rejected with `workspace_authority_mismatch`.
- Reverse-apply verification: without the adoption handling, `test_create_native_scratch_adopts_existing_valid_directory` fails with `FileExistsError`.
- SIF probe on DO-2: instrumented run of OpenHands OH-01 in the w39 SIF confirms the exact failure points and demonstrates progression past turn 0.

## 6) Rollout Plan

Require exact-head independent review and clean CI before merging into `main`. Merge `main` into profile lanes and re-run installed replays.

## 7) Rollback Plan

Revert the commit through standard protected PR review. No state or schema migrations are involved.

## 8) Approvals

Independent exact-head reviewer required. This document grants no merge authority.
