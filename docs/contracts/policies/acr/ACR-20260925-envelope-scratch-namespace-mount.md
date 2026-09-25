# ACR-20260925-envelope-scratch-namespace-mount

- `acr_id`: `ACR-20260925-envelope-scratch-namespace-mount`
- `title`: Mount the envelope scratch tmpfs on a descriptor opened inside the envelope mount namespace
- `author`: BreadBoard E4 containment
- `date`: 2026-09-25
- `status`: implemented (local scoped proof; Linux gate on DO-2 and independent exact-head review required)

## 1) Problem Statement

Since PR #145 (commit cc2eaf21), every Linux envelope launch fails in `_setup_mount_view` with `envelope_launch_failed`. The failure frame reports phase `mount_view` and errno 22 (EINVAL).

- **Evidence:** DO-2 Hermes installed replay job 1328 failed all six cases. Diagnostic job 1337 recorded `phase='mount_view'`, `errno_value=22`, `message='[Errno 22] Invalid argument'`, with the lease root at `/out/.../episode/lease`, outside `/tmp`.
- **Cause:** cc2eaf21 replaced the pathname scratch mount with `_mount_tmpfs(f"/proc/self/fd/{scratch_fd}", ...)`. `scratch_fd` is opened in the supervisor before the launcher calls `unshare(CLONE_NEWNS)`, so its `struct path` names a mount of the parent mount namespace. `mount(2)` onto a mountpoint whose mount is not in the caller's namespace fails with EINVAL (`check_mnt` in `do_add_mount`).
- **Why no check caught it:** the mocked unit test pinned the target string `/proc/self/fd/{scratch_fd}`. The real-launch tests are gated on sealed-execution hosts and did not run on the review hosts.

## 2) Scope and Surfaces

- **`breadboard/rl/harness/lease_envelope.py`**
  - `_prepare_lease_mountpoint` returns whether it recreated the mountpoint inside the fresh lease `/tmp`.
  - The new `_namespace_mountpoint_fd(target, source_fd, *, recreated)` opens the target inside the envelope mount namespace with `O_PATH | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC`.
    - It requires `(st_dev, st_ino)` to equal the verified scratch descriptor, unless the mountpoint was recreated inside the symlink-free lease `/tmp`. In that case the recreated directory is necessarily a new inode, and the original scratch is hidden from the view.
    - On a mismatch it fails with ESTALE. A final-component symlink fails with ENOTDIR.
  - `_setup_mount_view` mounts the scratch tmpfs on `/proc/self/fd/<that descriptor>` and closes the descriptor on every path. `_verify_mount_view` still requires the tmpfs to appear at the scratch path.
- **`tests/rl/harness/test_sandbox_process_integration.py`**
  - The wiring assertion on the inherited-descriptor target string is removed.
  - `test_envelope_mounts_scratch_on_bounded_private_tmpfs` asserts that the scratch tmpfs targets a descriptor reopened after namespace entry, not the inherited one, and that the descriptor names the verified scratch inode.
  - `test_envelope_refuses_scratch_replaced_before_its_mount[directory|symlink]` asserts that a scratch replaced after the pre-mount check is refused before any scratch mount.
  - Both tests are Linux-only (`O_PATH`).
- Kernel danger-zone: yes, `breadboard/rl/harness/lease_envelope.py` is under the kernel protected surface (`breadboard/**`).

## 3) Coupling and Generalization Impact

- Only the scratch mount target changes. The workspace bind (`open_tree` / `move_mount`), the `/tmp` tmpfs, proc and the mount-view verification are unchanged.
- The lease-level scratch identity checks from ACR-20260925-workspace-identity-native-scratch still apply before fork (`envelope_scratch_mismatch`).

## 4) Change Classification

- Classification: `behavioral-change`. Envelope launches with a scratch outside the lease `/tmp` work again, and a scratch swap between check and mount fails closed.
- Compatibility window: none.
- Schema bump: none.

## 5) Evidence and Validation Plan

- **Local (macOS):** `test_sandbox_process_integration.py` gives 23 passed and 52 skipped. The new tests are Linux-only.
- **DO-2 Linux gate, required:**
  - Run `test_sandbox_runtime.py` and `test_sandbox_process_integration.py` as root and as an unprivileged user, at base cfa558eb and at this head. At the base, the real-launch tests are expected to fail with EINVAL; at this head, all must pass.
  - Rerun the Hermes installed replay on a SIF built from a lane that contains this change.

## 6) Rollout Plan

1. Run the DO-2 Linux gate.
2. Obtain an independent exact-head review, green CI and zero unresolved threads.
3. Merge to main.
4. Merge main into the E4 lanes, rebuild the lane SIFs and rerun their installed replays.

## 7) Rollback Plan

Revert the commit. This returns to the cc2eaf21 behavior, under which every Linux envelope launch fails EINVAL. Reverting both this commit and the cc2eaf21 line instead returns to the pathname mount, whose swap window `_verify_mount_view` closes only after the mount.

## 8) Approvals

- Kernel reviewer: independent exact-head review required.
- Contracts reviewer: independent exact-head review required.
- Final decision: Main retains promotion authority; this ACR does not authorize merge.
