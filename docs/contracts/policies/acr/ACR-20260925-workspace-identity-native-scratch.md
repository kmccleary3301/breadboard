# ACR-20260925-workspace-identity-native-scratch

- `acr_id`: `ACR-20260925-workspace-identity-native-scratch`
- `title`: Attested trusted-process turn-0 chain: single-owner native scratch and script-command fd inheritance
- `author`: BreadBoard E4 containment lane
- `date`: 2026-09-25
- `status`: implemented; independent exact-head review pending

## 1) Problem Statement

Following PR #144 (which resolved envelope launch EROFS on `/tmp`), trusted process envelope runs inside Apptainer SIFs encountered failures at turn 0 across three distinct lifecycle mechanisms:

1. **Native Scratch Authority Mismatch**:
   - In attested containment mode (`RuntimeContainment.ATTESTED`), `TrustedProcessBackend.launch` creates the native scratch directory (`scratch = context.native_scratch_path`).
   - During subsequent native phase invocation (`invoke_native_phase`), `_create_native_scratch` attempted an unconditioned `os.mkdir(name, mode=0o700, dir_fd=root_fd)`. Because the scratch directory was already created at launch, `os.mkdir` raised `FileExistsError`, caught and re-raised as `WorkspaceStateError('native scratch authority is unavailable', code='workspace_authority_mismatch')`.
   - Additionally, descriptor identity checks in `TrustedProcessHandle._start_stopped_process` require that the workspace descriptor identity (`os.fstat(self._workspace_fd)`) strictly matches `self._workspace_identity` captured at materialization.
   - When `TrustedProcessBackend.launch` refused a pre-existing scratch directory with `runtime_preflight_failed`, subsequent launch-failure cleanup in `SandboxRuntimeManager.open` previously invoked `_remove_native_scratch` unconditionally, silently deleting the stale/foreign directory that caused the launch refusal rather than preserving and quarantining it.
   - During envelope launch, reopening the scratch directory by pathname inside `launch_envelope` left a TOCTOU replacement window before the envelope namespace was constructed.
   - When `open_verifier` encountered a launch refusal due to a pre-existing `verifier-lease-*.native-scratch`, its exception cleanup called `_remove_native_scratch(self, lease_id)` without an expected identity, deleting the foreign/pre-existing directory instead of preserving and quarantining it.
   - Inside the envelope, `_setup_mount_view` verified `scratch_fd` identity but mounted tmpfs using the `scratch` pathname, leaving a replace-after-check window.
   - Launch created the scratch by pathname and reopened it by pathname, and on envelope failure removed it with a pathname `rmdir`, so a same-EUID substitution could be adopted or a replacement deleted.
2. **Envelope Script-Command Descriptor Closure (Verifier FD)**:
   - Script-format commands executed via `TrustedProcessHandle.run_argv` (such as verifier snapshot-integrity scripts) build `execution_argv = (shell_fd_path, command_fd_path, *argv[1:])` and wrap execution in `(shell_fd_path, '-lc', 'exec "$@"', 'breadboard-execute', *execution_argv)`.
   - In envelope mode, `spawn_envelope_process` and `_prepare_exec_descriptors` placed the shell executable at `fd 3` and rewrote `argv[4]` to `/proc/self/fd/3`.
   - Before `execveat`, `_envelope_child` sets `os.set_inheritable(exec_fd, False)` on `fd 3` to enforce close-on-exec.
   - Upon `execveat`, `fd 3` was closed by the kernel. When the outer shell evaluated `exec "$@"`, `$1` was `/proc/self/fd/3`, which no longer existed, failing with `rc 127` (`breadboard-execute: line 1: /proc/self/fd/3: No such file or directory`).
   - ELF commands do not reference the shell path in `"$@"`, so only script-format commands suffered this failure. Non-envelope execution avoids this because `pass_fds` keeps executables open.

3. **Verifier Lease Cleanup Rejects Attested Native Scratch**:
   - Every attested lease, including verifier leases, receives native scratch because `launch_envelope` mounts it as the envelope's private writable home. `VerifierWorkspaceLease.close` therefore emits a `native_scratch` cleanup step.
   - `BreadBoardV2EpisodeService` checked verifier cleanup with `_cleanup_released(..., required={runtime, workspace, snapshot, lease_record})`, which allowed only the required set, so a released `native_scratch` step marked verifier cleanup as not released (`verifier_cleanup_not_released`) after a successful verifier (returncode 0, DO-2 job 1304).
   - The failed-completed tombstone then failed `_validate_cleanup_projection` (`cleanup receipt resource set is incomplete or ambiguous`) because only primary cleanup admitted optional `native_scratch`. Pre-existing on `main`.

## 2) Scope and Surfaces

- `breadboard.rl.harness.sandbox`:
  - `TrustedProcessBackend.launch`: exclusive scratch creation with mode `0700` and `native_scratch_identity` binding on `TrustedProcessHandle`.
  - `_create_native_scratch`: identity verification (never `mkdir` when expected identity is provided; unconfined keeps create-exclusive).
  - `TrustedProcessBackend.launch`: pass verified `scratch_fd` and `native_scratch_identity` into `launch_envelope`; notify `record_scratch_identity` callback only when this launch created the scratch directory.
  - `TrustedProcessBackend.launch`: requires `RuntimeLaunchContext.lease_root_identity` (supplied by `SandboxRuntimeManager` from its pinned lease-root descriptor); opens the scratch parent with `O_NOFOLLOW | O_DIRECTORY`, requires its `(st_dev, st_ino)` to equal the lease-root identity, then `mkdir`/`open` the scratch relative to that descriptor and requires it to be empty. Launch no longer removes scratch itself; failure cleanup is owned by the manager's identity-bound `_cleanup_native_scratch_step`.
  - `SandboxRuntimeManager.open`: track created scratch identity via context callback; only clean up created scratch instances on failure, preserving pre-existing scratch directories with a `QUARANTINED` step (`preexisting_scratch_preserved`).
  - `_remove_native_scratch`: enforce `expected_identity` check with leak-free `try/finally` around descriptor operations, quarantining mismatches as `scratch_identity_mismatch` while preserving the directory.
  - `open_verifier`: track created scratch identity via `created_scratch_identities` and pass `record_scratch_identity` to `_launch_context`; use shared `_cleanup_native_scratch_step` helper in both `open` and `open_verifier` to preserve pre-existing scratch directories with `CleanupStepReceipt('native_scratch', QUARANTINED, 'preexisting_scratch_preserved')`.
- `breadboard.rl.harness.lease_envelope`:
  - `launch_envelope`: require `scratch_fd` and `scratch_identity` keyword arguments. Duplicate via `/proc/self/fd/{scratch_fd}` with `O_PATH | O_DIRECTORY | O_CLOEXEC` and verify directory mode and exact `(st_dev, st_ino)` identity prior to fork, failing closed with `EnvelopeLaunchError(code='envelope_scratch_mismatch', phase='scratch_verify')`.
  - `_prepare_exec_descriptors`: after the existing low packing, when any argv element (index >= 1) references `exec_fd`, duplicate the packed exec descriptor to one inheritable descriptor numbered above every live source/target and rewrite those argv elements to it; the exec target itself remains close-on-exec (`CLOEXEC`).
  - `_setup_mount_view`: mount scratch tmpfs directly onto descriptor target `f"/proc/self/fd/{scratch_fd}"` instead of pathname, eliminating the replace-after-check window; verified post-mount via `_verify_mount_view`.
- `breadboard.rl.harness.service` / `breadboard.rl.harness.evidence`:
  - Verifier cleanup admits optional `native_scratch` exactly as primary cleanup does (`_cleanup_released(..., optional={"native_scratch"})`, `_VERIFIER_OPTIONAL_CLEANUP_RESOURCES`). A present `native_scratch` step must still be `RELEASED`/`ALREADY_RELEASED`; unknown resources remain rejected.
- `tests/rl/harness/test_sandbox_runtime`: portable regression tests for scratch identity adoption, swapped-directory rejection, invalid preexisting entry rejection, and unconfined preexisting directory rejection.
- `tests/rl/harness/test_sandbox_process_integration`:
  - Sealed execution integration tests for preexisting scratch rejection and lifecycle identity adoption under `@requires_sealed_execution`.
- Pure unit test `test_prepare_exec_descriptors_script_format_argv_fd_mapping` and collision test `test_prepare_exec_descriptors_high_source_collision_resistance` for script-format argv/fd mapping, non-collision under fragmented descriptor spaces, and inheritability preservation on macOS and Linux.
  - Integration test `test_pinned_script_verifier_in_envelope_executes_with_open_descriptor_argv` verifying script-format command execution inside the envelope with open descriptor argv.
- Danger-zone: yes. Protected kernel/containment surface. Strict single-owner enforcement; no blind adoption of pre-existing unverified directories; all checks fail-closed.

## 3) Coupling and Generalization Impact

1. **Native Scratch Single-Owner Authority**:
   - Exclusive Launch Creation & Binding: Under `RuntimeContainment.ATTESTED`, `TrustedProcessBackend.launch` exclusively creates the native scratch directory (`os.mkdir(scratch, mode=0o700)`). If the directory already exists (`FileExistsError`), launch fails closed with `SandboxLaunchError('attested trusted process native scratch already exists', code='runtime_preflight_failed')`. Upon creation, `launch` inspects the directory and its parent under `O_NOFOLLOW | O_DIRECTORY`, verifies ownership (`euid`), mode (`0700`), name, and matching device (`st_dev == parent.st_dev`), and captures `(st_dev, st_ino)` as `native_scratch_identity` on `TrustedProcessHandle`.
   - Strict Native Phase Adoption: When `invoke_native_phase` runs (e.g. `initialize`), `_create_native_scratch` receives `expected_identity=lease._runtime.native_scratch_identity`. If `expected_identity` is present, `_create_native_scratch` never calls `mkdir`; instead, it opens the existing entry under `manager._lease_root_fd` with `O_NOFOLLOW | O_DIRECTORY` and verifies directory type, `euid` ownership, `0700` mode, same device, and matching `(st_dev, st_ino)`.
   - Unconfined Preservation: If `expected_identity is None` (unconfined test path), `_create_native_scratch` retains create-exclusive semantics via `os.mkdir(..., dir_fd=root_fd)`.
   - Pre-existing Scratch Preservation and Quarantine: When launch is refused due to a pre-existing scratch directory, `SandboxRuntimeManager.open` preserves the foreign/stale directory intact (including all sentinel contents) and records `CleanupStepReceipt('native_scratch', CleanupState.QUARANTINED, 'preexisting_scratch_preserved')`. `_remove_native_scratch` validates `expected_identity` under `try/finally` descriptor cleanup and quarantines mismatches as `scratch_identity_mismatch`.
   - Descriptor-Pinned Envelope Scratch Verification: `launch_envelope` receives pinned `scratch_fd` and `scratch_identity`. It never opens scratch by pathname; instead, it duplicates `/proc/self/fd/{scratch_fd}` under `O_PATH | O_DIRECTORY | O_CLOEXEC` and validates `(st_dev, st_ino) == scratch_identity` before `fork()`, raising `EnvelopeLaunchError(code='envelope_scratch_mismatch', phase='scratch_verify')` on mismatch without leaking file descriptors.
   - Lease-Root-Pinned Creation: scratch creation and the first open are relative to a parent descriptor verified against the manager's lease-root identity; a non-empty or otherwise invalid created entry fails closed and is preserved (quarantined) rather than deleted. The residual POSIX `mkdirat`→`openat` window is bounded by these checks and documented in code.
2. **Envelope Descriptor Inheritance for Pinned Script Executables**:
   - `_prepare_exec_descriptors` keeps the original low packing of the exec and argv-named descriptors. If any argv element at index >= 1 references `exec_fd`, the packed exec descriptor is duplicated (`dup2(..., inheritable=True)`) to `max(all fds, sources, targets, status_fd, exec_ready_fd) + 1`, which cannot collide with any live descriptor.
   - Those argv elements are rewritten to `/proc/self/fd/<extra>`; index 0 keeps the packed exec target.
   - The exec target is marked non-inheritable (`CLOEXEC`) before `_execveat_fd`; the extra duplicate stays open in the child, matching the non-envelope path where `pass_fds` keeps the sealed shell descriptor open.
   - Pinned descriptor semantics are fully preserved without path-based fallback or unpinned reopening.

3. **Verifier Cleanup Resource Contract**:
   - Verifier cleanup required resources are unchanged; `native_scratch` becomes an optional member for verifier receipts in both the service release check and evidence projection validation, mirroring `_PRIMARY_OPTIONAL_CLEANUP_RESOURCES`.

## 4) Change Classification

- Classification: `additive`.

Fail-closed, corrective changes across the turn-0 pipeline. No permissions, containment boundaries, or descriptor pinning constraints are loosened.

## 5) Evidence and Validation Plan

- **Scratch Single Owner**:
  - Diagnosed in DO-2 probe jobs 1286/1288 (`SCR/w44-scratch-probe.py`).
  - Unit tests in `test_sandbox_runtime.py`: `test_unconfined_create_native_scratch_rejects_preexisting_directory`, `test_create_native_scratch_adopts_matching_identity`, `test_create_native_scratch_rejects_swapped_directory_identity`, `test_create_native_scratch_rejects_existing_invalid_directory`.
  - Integration tests in `test_sandbox_process_integration.py`: `test_sealed_attested_launch_rejects_preexisting_scratch_directory` (asserting sentinel preservation and `preexisting_scratch_preserved` quarantine receipt), `test_sealed_attested_launch_and_native_scratch_lifecycle`, and `test_launch_envelope_scratch_identity_mismatch_fails_before_fork` (asserting `envelope_scratch_mismatch` error before fork).
  - Unit tests in `test_sandbox_runtime.py`: `test_remove_native_scratch_rejects_identity_mismatch_and_preserves_directory` and `test_open_preserves_preexisting_native_scratch_on_preflight_failure`.
  - Unit test in `test_sandbox_runtime.py`: `test_open_verifier_preserves_preexisting_native_scratch_on_preflight_failure` (asserting sentinel and pre-existing scratch preservation with `preexisting_scratch_preserved` quarantine receipt).
  - Unit test in `test_sandbox_process_integration.py`: `test_envelope_mounts_scratch_on_bounded_private_tmpfs` (asserting `_mount_tmpfs` target is descriptor path `f"/proc/self/fd/{scratch_fd}"`, not scratch pathname).
  - Unit tests in `test_sandbox_runtime.py`: `test_attested_launch_rejects_lease_root_identity_mismatch_without_scratch_creation`, `test_attested_launch_rejects_non_empty_scratch_preserves_directory_in_quarantine`, `test_attested_launch_envelope_failure_cleans_up_or_quarantines_replaced_scratch`; sealed counterparts in `test_sandbox_process_integration.py`.
- **Envelope Script-Command Descriptor**:
  - Diagnosed in DO-2 jobs 1294/1295 (`breadboard-execute: line 1: /proc/self/fd/3: No such file or directory`, rc 127 during verifier snapshot integrity check).
- Unit tests in `test_sandbox_process_integration.py`: `test_prepare_exec_descriptors_script_format_argv_fd_mapping` and `test_prepare_exec_descriptors_high_source_collision_resistance` running on macOS and Linux, validating distinct inheritable descriptor allocation, collision resistance under fragmented descriptor spaces, and CLOEXEC isolation.
  - Integration test in `test_sandbox_process_integration.py`: `test_pinned_script_verifier_in_envelope_executes_with_open_descriptor_argv` verifying script-format verifier execution inside the envelope and proving all argv descriptor paths remain open.
- **Reverse-apply verification**:
  - Reverse diff of `lease_envelope.py` against `test_prepare_exec_descriptors_script_format_argv_fd_mapping` demonstrates exact failure (`assert 3 != 3`) before the fix and pass after.

- **Verifier Cleanup Resource Contract**:
  - Diagnosed in DO-2 job 1304 (verifier returncode 0; `verifier_cleanup_not_released`, then `EvidenceValidationError` at `_validate_cleanup_projection`).
  - `test_v2_service.py`: `test_verifier_native_scratch_cleanup_admission` (released → succeeded; quarantined → `verifier_cleanup_not_released`), `test_verifier_cleanup_released_admissions`.
  - `test_evidence.py`: `test_failed_completed_publication_admits_verifier_native_scratch_and_rejects_unknown_resource`.
  - Reverse-apply: with `evidence.py`/`service.py` from 39fafaf2 the new tests fail (3 failed); with the fix they pass.

## 6) Rollout Plan

Require exact-head independent review and clean CI before merging PR #145 into `main`. Merge `main` into profile lanes and re-run installed replays.

## 7) Rollback Plan

Revert the commit through standard protected PR review. No state or schema migrations are involved.

## 8) Approvals

Independent exact-head reviewer required. This document grants no merge authority.
