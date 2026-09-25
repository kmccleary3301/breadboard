# ACR-20260925-workspace-identity-native-scratch

- `acr_id`: `ACR-20260925-workspace-identity-native-scratch`
- `title`: Attested trusted-process strict single-owner native scratch identity recording and verification
- `author`: BreadBoard E4 containment lane
- `date`: 2026-09-25
- `status`: implemented; independent exact-head review pending

## 1) Problem Statement

Following PR #144 (which resolved envelope launch EROFS on `/tmp`), trusted process envelope runs inside Apptainer SIFs encountered `WorkspaceStateError` with `code="workspace_authority_mismatch"` at turn 0:
1. In attested containment mode (`RuntimeContainment.ATTESTED`), `TrustedProcessBackend.launch` creates the native scratch directory (`scratch = context.native_scratch_path`).
2. During the subsequent native phase invocation (`invoke_native_phase`), `_create_native_scratch` attempted an unconditioned `os.mkdir(name, mode=0o700, dir_fd=root_fd)`. Because the scratch directory was already created at launch, `os.mkdir` raised `FileExistsError`, which was caught and re-raised as `WorkspaceStateError('native scratch authority is unavailable', code='workspace_authority_mismatch')`.
3. Additionally, descriptor identity checks in `TrustedProcessHandle._start_stopped_process` require that the workspace descriptor identity (`os.fstat(self._workspace_fd)`) strictly matches `self._workspace_identity` captured at materialization.

## 2) Scope and Surfaces

- `breadboard.rl.harness.sandbox`: `TrustedProcessBackend.launch` exclusive scratch creation and `native_scratch_identity` binding on `TrustedProcessHandle`; `_create_native_scratch` identity verification (never `mkdir` when expected identity is provided; unconfined keeps create-exclusive).
- `tests/rl/harness/test_sandbox_runtime`: portable regression tests for scratch identity adoption, swapped-directory rejection, invalid preexisting entry rejection, and unconfined preexisting directory rejection.
- `tests/rl/harness/test_sandbox_process_integration`: regression tests for sealed attested launch preexisting scratch rejection and lifecycle identity adoption under `@requires_sealed_execution`.
- Danger-zone: yes. Protected kernel/containment surface. Strict single-owner enforcement; no blind adoption of pre-existing unverified directories; all checks fail-closed.

## 3) Coupling and Generalization Impact

The native scratch design enforces strict single-owner authority between launch and native phase execution:
1. Exclusive Launch Creation & Binding: Under `RuntimeContainment.ATTESTED`, `TrustedProcessBackend.launch` exclusively creates the native scratch directory (`os.mkdir(scratch, mode=0o700)`). If the directory already exists (`FileExistsError`), launch fails closed with `SandboxLaunchError('attested trusted process native scratch already exists', code='runtime_preflight_failed')`. Upon creation, `launch` inspects the directory and its parent under `O_NOFOLLOW | O_DIRECTORY`, verifies ownership (`euid`), mode (`0700`), name, and matching device (`st_dev == parent.st_dev`), and captures `(st_dev, st_ino)` as `native_scratch_identity` on `TrustedProcessHandle`.
2. Strict Native Phase Adoption: When `invoke_native_phase` runs (e.g. `initialize`), `_create_native_scratch` receives `expected_identity=lease._runtime.native_scratch_identity`. If `expected_identity` is present, `_create_native_scratch` never calls `mkdir`; instead, it opens the existing entry under `manager._lease_root_fd` with `O_NOFOLLOW | O_DIRECTORY` and verifies:
   - The entry is a directory (`stat.S_ISDIR`).
   - The entry is owned by the running effective UID (`st_uid == os.geteuid()`).
   - The entry permissions are strictly `0700` (`stat.S_IMODE == 0o700`).
   - The entry resides on the same device as the lease root (`st_dev == root_device`).
   - The entry's identity strictly matches the launch-recorded identity (`(st_dev, st_ino) == expected_identity`).
   If any check fails (or if the directory was swapped), it fails closed with `WorkspaceStateError(code="workspace_authority_mismatch")`.
3. Unconfined Preservation: If `expected_identity is None` (unconfined test path), `_create_native_scratch` retains create-exclusive semantics via `os.mkdir(..., dir_fd=root_fd)`. If the directory exists, `FileExistsError` is caught and raised as `WorkspaceStateError(code="workspace_authority_mismatch")`.
4. Cleanup: Semantics in `_remove_native_scratch` and `_native_scratch_present` remain unchanged.
## 4) Change Classification

- Classification: `additive`.

Fail-closed, corrective change. No permissions or identity boundaries are loosened; authority checks are preserved and strictly verified across both creation and adoption.

## 5) Evidence and Validation Plan

- Portable unit tests in `tests/rl/harness/test_sandbox_runtime`:
  - `test_unconfined_create_native_scratch_rejects_preexisting_directory`: unconfined path fails closed with `workspace_authority_mismatch` on existing directory.
  - `test_create_native_scratch_adopts_matching_identity`: adopting matching `(st_dev, st_ino)` succeeds and cleans up cleanly.
  - `test_create_native_scratch_rejects_swapped_directory_identity`: swapped directory with new inode fails closed with `workspace_authority_mismatch`.
  - `test_create_native_scratch_rejects_existing_invalid_directory`: non-directory file or `0777` permissions fails closed with `workspace_authority_mismatch`.
- Sealed execution integration tests in `tests/rl/harness/test_sandbox_process_integration`:
  - `test_sealed_attested_launch_rejects_preexisting_scratch_directory`: pre-existing scratch causes launch preflight failure (`runtime_preflight_failed`).
  - `test_sealed_attested_launch_and_native_scratch_lifecycle`: end-to-end sealed launch records identity and native phase adopts the exact matching identity.
- Reverse-apply verification: `git diff 5ec075de -- breadboard/rl/harness/sandbox.py > SCR/w48.patch; git apply -R SCR/w48.patch; <pytest runtime tests fail>; git apply SCR/w48.patch; <pytest runtime tests pass>`.
- Probe verification: diagnosed via DO-2 probe jobs 1286/1288 (`SCR/w44-scratch-probe.py` at `d72be4be`).

## 6) Rollout Plan

Require exact-head independent review and clean CI before merging into `main`. Merge `main` into profile lanes and re-run installed replays.

## 7) Rollback Plan

Revert the commit through standard protected PR review. No state or schema migrations are involved.

## 8) Approvals

Independent exact-head reviewer required. This document grants no merge authority.
