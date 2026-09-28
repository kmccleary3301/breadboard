# ACR-20260924-openclaw-marker-sealed-image

- `acr_id`: `ACR-20260924-openclaw-marker-sealed-image`
- `title`: Spawn the OpenClaw cleanup marker from the worker's sealed image
- `author`: BreadBoard E4 OpenClaw implementation
- `date`: 2026-09-24
- `status`: implemented (local scoped proof; independent exact-head review and installed Linux replay required)

## 1) Problem Statement

The sandbox launches the Node worker from a sealed memfd snapshot (`sandbox.py` `_snapshot_installed_executable`, executed through its `/proc/self/fd` path). Inside that worker, `process.execPath` is `/memfd:breadboard-runtime (deleted)`. At close, `markerObservation` in `openclaw_tool_worker.mjs` spawned `process.execPath` to prove that the OS observer can see and kill a process group. That spawn failed with ENOENT. The child had no `'error'` listener, so the worker crashed and every installed case ended in `native_finalization_failed` (DO-2 job 1212). The same crash happens off Linux whenever the launch path has been deleted.

## 2) Scope and Surfaces

- Extension: `breadboard/rl/harness/openclaw_tool_worker.mjs` (`markerObservation` and the fatal error frame type).
- Tests: `tests/rl/harness/test_openclaw_2026_9_4_native_tools.py`.
- Contract surfaces: the close-phase marker executable, and the `type` of the worker's fatal error frame for marker failures. The frame keeps its exact `{type, message}` shape.
- Kernel danger-zone: yes, because the source worker under `breadboard/**` changes. The generic Conductor, sandbox launch, and other profiles remain unchanged.

## 3) Coupling and Generalization Impact

- On Linux the marker always runs `/proc/self/exe`, the kernel link to the worker's own executable image. For a sealed launch this is the same memfd inode, so the sealed-binary guarantee holds. If `/proc/self/exe` is absent or not executable, the worker raises a typed `OpenClawMarkerObservationError`. It never falls back to `process.execPath` on Linux.
- Off Linux the marker keeps `process.execPath`. Sealed launches are Linux-only (`sandbox.py` rejects them elsewhere as `runtime_unsupported`), so no sealed image exists to preserve there, and the darwin close-path tests keep running.
- A spawn `'error'` event now rejects the observation with `OpenClawMarkerObservationError` on both branches, so a failed spawn becomes an error frame instead of an uncaught crash. A marker group that cannot be proven raises the same typed error. The group is still proven before the kill, as before.
- The fatal frame's `type` is `OpenClawMarkerObservationError` for these failures and stays `OpenClawWorkerError` for all others. `native_session.py` already accepts any string type and records it as `error_type`. No Python consumer matches the old literal.
- The cleanup receipt (`marker_before`, `marker_after`, `process_groups`, `all_dead`) keeps its keys. On Linux, `marker_before[].argv` now starts with `/proc/self/exe`.

## 4) Change Classification

- Classification: `behavioral-change` (the Linux marker executable changes, and marker failures return a typed frame instead of crashing the worker).
- Compatibility window: none; only the pinned OpenClaw 2026.9.4 worker changes.
- Schema bump: none.

## 5) Evidence and Validation Plan

- Pre-fix reproduction: on darwin, `test_marker_spawn_failure_is_a_typed_error_not_a_worker_crash` launches the worker from a hardlinked node, then unlinks it. At base `d6b22e32` the worker died with an unhandled `'error'` event (`spawn … ENOENT`). With the fix it returns an `OpenClawMarkerObservationError` frame and keeps serving phases.
- Linux-gated `test_marker_runs_the_sealed_worker_image_under_memfd_launch` runs the worker from a sealed memfd, the product's launch shape. It asserts that the worker's `/proc/<pid>/exe` is the memfd inode, that close proves all processes dead, and that the marker is the worker's child running `/proc/self/exe`. It is skipped on darwin and must run on Linux (CI or installed replay).
- The Linux fail-closed branch was smoke-run on darwin with `process.platform` forced to `linux`. With `/proc/self/exe` absent, close returned the typed error and did not fall back to `process.execPath`.
- Each OpenClaw test file runs separately. `scripts/check_danger_zone_acr.py` checks the changed-file set, and `scripts/check_kernel_contract_pack_v1.py` checks the kernel pack.

## 6) Rollout Plan

Independent review must inspect the marker executable selection, the typed error path, and the Linux-gated test on the exact head. Main owns the Linux run of the gated test and the installed replay that retries DO-2 job 1212's six cases. Local darwin proof does not establish installed acceptance.

## 7) Rollback Plan

Revert this commit on a new branch if the installed replay shows the marker failing to prove its group, or a close regression. Preserve raw evidence and rerun the four OpenClaw test files before another promotion attempt. Do not restore an unsealed filesystem path on Linux as a partial rollback.

## 8) Approvals

- Kernel reviewer: independent exact-head review required.
- Contracts reviewer: independent exact-head review required.
- Ops reviewer: required before installed promotion.
- Final decision: Main retains promotion authority; this ACR does not authorize merge.
