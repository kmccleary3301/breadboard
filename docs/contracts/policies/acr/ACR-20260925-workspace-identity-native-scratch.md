# ACR-20260925-workspace-identity-native-scratch

- `acr_id`: `ACR-20260925-workspace-identity-native-scratch`
- `title`: Attested trusted-process turn-0 chain: single-owner native scratch and script-command fd inheritance
- `author`: BreadBoard E4 containment lane
- `date`: 2026-09-25
- `status`: implemented; independent exact-head review pending

## 1) Problem Statement

Following PR #144 (which resolved envelope launch EROFS on `/tmp`), trusted process envelope runs inside Apptainer SIFs encountered failures at turn 0 across two distinct lifecycle mechanisms:

1. **Native Scratch Authority Mismatch**:
   - In attested containment mode (`RuntimeContainment.ATTESTED`), `TrustedProcessBackend.launch` creates the native scratch directory (`scratch = context.native_scratch_path`).
   - During subsequent native phase invocation (`invoke_native_phase`), `_create_native_scratch` attempted an unconditioned `os.mkdir(name, mode=0o700, dir_fd=root_fd)`. Because the scratch directory was already created at launch, `os.mkdir` raised `FileExistsError`, caught and re-raised as `WorkspaceStateError('native scratch authority is unavailable', code='workspace_authority_mismatch')`.
   - Additionally, descriptor identity checks in `TrustedProcessHandle._start_stopped_process` require that the workspace descriptor identity (`os.fstat(self._workspace_fd)`) strictly matches `self._workspace_identity` captured at materialization.

2. **Envelope Script-Command Descriptor Closure (Verifier FD)**:
   - Script-format commands executed via `TrustedProcessHandle.run_argv` (such as verifier snapshot-integrity scripts) build `execution_argv = (shell_fd_path, command_fd_path, *argv[1:])` and wrap execution in `(shell_fd_path, '-lc', 'exec "$@"', 'breadboard-execute', *execution_argv)`.
   - In envelope mode, `spawn_envelope_process` and `_prepare_exec_descriptors` placed the shell executable at `fd 3` and rewrote `argv[4]` to `/proc/self/fd/3`.
   - Before `execveat`, `_envelope_child` sets `os.set_inheritable(exec_fd, False)` on `fd 3` to enforce close-on-exec.
   - Upon `execveat`, `fd 3` was closed by the kernel. When the outer shell evaluated `exec "$@"`, `$1` was `/proc/self/fd/3`, which no longer existed, failing with `rc 127` (`breadboard-execute: line 1: /proc/self/fd/3: No such file or directory`).
   - ELF commands do not reference the shell path in `"$@"`, so only script-format commands suffered this failure. Non-envelope execution avoids this because `pass_fds` keeps executables open.

## 2) Scope and Surfaces

- `breadboard.rl.harness.sandbox`:
  - `TrustedProcessBackend.launch`: exclusive scratch creation with mode `0700` and `native_scratch_identity` binding on `TrustedProcessHandle`.
  - `_create_native_scratch`: identity verification (never `mkdir` when expected identity is provided; unconfined keeps create-exclusive).
- `breadboard.rl.harness.lease_envelope`:
  - `_prepare_exec_descriptors`: allocate a distinct inheritable duplicate descriptor for any argv element (index >= 1) referencing `exec_fd`, while `exec_fd` itself remains close-on-exec (`CLOEXEC`).
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

2. **Envelope Descriptor Inheritance for Pinned Script Executables**:
   - In `_prepare_exec_descriptors`, if any argv element at index >= 1 references `exec_fd` (`needs_inherited_exec`), a dedicated inheritable duplicate descriptor (`inherited_exec_fd`) is allocated.
   - Rewritten argv maps index >= 1 occurrences to `/proc/self/fd/{inherited_exec_fd}`, while index 0 maps to `/proc/self/fd/{target_exec_fd}` (or is replaced by `argv0_path`).
   - `target_exec_fd` is marked non-inheritable (`CLOEXEC`) before `_execveat_fd`, preventing descriptor leakage, while `inherited_exec_fd` remains open and inheritable in the child process.
   - Pinned descriptor semantics are fully preserved without path-based fallback or unpinned reopening.

## 4) Change Classification

- Classification: `additive`.

Fail-closed, corrective changes across the turn-0 pipeline. No permissions, containment boundaries, or descriptor pinning constraints are loosened.

## 5) Evidence and Validation Plan

- **Scratch Single Owner**:
  - Diagnosed in DO-2 probe jobs 1286/1288 (`SCR/w44-scratch-probe.py`).
  - Unit tests in `test_sandbox_runtime.py`: `test_unconfined_create_native_scratch_rejects_preexisting_directory`, `test_create_native_scratch_adopts_matching_identity`, `test_create_native_scratch_rejects_swapped_directory_identity`, `test_create_native_scratch_rejects_existing_invalid_directory`.
  - Integration tests in `test_sandbox_process_integration.py`: `test_sealed_attested_launch_rejects_preexisting_scratch_directory`, `test_sealed_attested_launch_and_native_scratch_lifecycle`.
- **Envelope Script-Command Descriptor**:
  - Diagnosed in DO-2 jobs 1294/1295 (`breadboard-execute: line 1: /proc/self/fd/3: No such file or directory`, rc 127 during verifier snapshot integrity check).
- Unit tests in `test_sandbox_process_integration.py`: `test_prepare_exec_descriptors_script_format_argv_fd_mapping` and `test_prepare_exec_descriptors_high_source_collision_resistance` running on macOS and Linux, validating distinct inheritable descriptor allocation, collision resistance under fragmented descriptor spaces, and CLOEXEC isolation.
  - Integration test in `test_sandbox_process_integration.py`: `test_pinned_script_verifier_in_envelope_executes_with_open_descriptor_argv` verifying script-format verifier execution inside the envelope and proving all argv descriptor paths remain open.
- **Reverse-apply verification**:
  - Reverse diff of `lease_envelope.py` against `test_prepare_exec_descriptors_script_format_argv_fd_mapping` demonstrates exact failure (`assert 3 != 3`) before the fix and pass after.

## 6) Rollout Plan

Require exact-head independent review and clean CI before merging PR #145 into `main`. Merge `main` into profile lanes and re-run installed replays.

## 7) Rollback Plan

Revert the commit through standard protected PR review. No state or schema migrations are involved.

## 8) Approvals

Independent exact-head reviewer required. This document grants no merge authority.
