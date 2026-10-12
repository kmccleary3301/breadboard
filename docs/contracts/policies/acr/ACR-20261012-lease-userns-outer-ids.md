# ACR-20261012-lease-userns-outer-ids

- `acr_id`: `ACR-20261012-lease-userns-outer-ids`
- `title`: Map the caller's outer IDs in the lease user namespace
- `author`: BreadBoard RL implementation
- `date`: 2026-10-12
- `status`: implemented

## 1) Problem Statement

`_enter_user_namespace` (`breadboard/rl/harness/lease_envelope.py`) read `os.geteuid()` and `os.getegid()` after `unshare(CLONE_NEWUSER)`. Inside a new user namespace those calls report the overflow IDs until the maps are written. So both `preflight_host_containment` and `_launcher_main` wrote `0 65534 1` to `/proc/self/uid_map`, and the kernel refused it with EPERM at the `uid_map` write.

On any host without the privileged namespaces, the unprivileged user-namespace fallback therefore never succeeded. Attested containment was refused as `runtime_unsupported` ("Linux namespace setup or UID/GID mapping is unavailable"). That includes an Apptainer `--fakeroot` task SIF once the BreadBoard agent's `isolate.py` drops CAP_SYS_ADMIN.

Measured on IBM Slurm, 2026-10-12, in the astropy-12907 task SIF after `isolate.py`:
- Probe job 176954 failed containment.
- Diagnostic 176960 traced it to the EPERM `uid_map` write.
- Diagnostic 176959 showed that raw `unshare(USER|PID|NS|NET)` with `0 <outer uid> 1` maps succeeds in the same position.

## 2) Scope and Surfaces

- Kernel danger-zone: yes, under `breadboard/**`. The change touches `breadboard/rl/harness/lease_envelope.py` only.
- `preflight_host_containment` and `_launcher_main` read the effective UID/GID before the combined `unshare(CLONE_NEWUSER | CLONE_NEWPID | CLONE_NEWNS | CLONE_NEWNET)` and pass them to `_enter_user_namespace(uid, gid)`. That function writes `setgroups deny` and the `0 <uid> 1` / `0 <gid> 1` maps, unchanged in form.
- Not changed:
  - the privileged-namespace first attempt and its EPERM-only fallback condition;
  - the namespace flag sets;
  - the map contents' shape;
  - error typing (`EnvelopeUnsupportedHostError`, `EnvelopeLaunchError`);
  - every other launcher step.

## 3) Coupling and Generalization Impact

- Harness- and task-neutral.
- The fallback now maps namespace root to the identity that already owns the lease's files outside the namespace. Before the change no host could take this lane, so no host relied on the overflow mapping.
- Hosts that grant the privileged namespaces take the same path as before.

## 4) Change Classification

- Classification: `behavioral-change` (a bug fix). Containment that was refused on unprivileged hosts is now established. There is no schema or wire-format change.

## 5) Evidence and Validation Plan

- New real-execution test `tests/rl/harness/test_sandbox_process_integration.py::test_preflight_maps_outer_ids_in_an_unprivileged_user_namespace` runs the real `preflight_host_containment` in a real unprivileged user namespace.
  - Its module-level host probe uses the module's ctypes `_unshare`, which works on every supported Python including 3.11. It makes the production calls: the privileged flags first, then the combined user-namespace flags mapped to the outer IDs.
  - The probe turns every host refusal into a skip reason rather than a collection error.
- IBM Linux job 176967 (cnode-5) ran the task SIF with `--writable-tmpfs --fakeroot --no-home --no-mount tmp --cleanenv`, through `isolate.py`:
  - Before (main `4bfd98f2`): 12 passed, 7 skipped. All 7 skips were `runtime_unsupported`.
  - After: 20 passed, 0 skipped. The 20 include the lease-envelope launch tests and the new test.
  - Job 176963 gave the same result on the RL runtime lineage.
- The revised probe's A/B is recorded on the PR.

## 6) Rollout Plan

- RL runtime lineage: runtime `bbagent-runtime/e4c2` carries the fix (wheel sha256 `303d045c…`).
- Main: this PR.

## 7) Rollback Plan

- Revert the commit. There is no configuration surface.

## 8) Approvals

- This artifact records the implementation decision and its evidence. It does not assert external PR acceptance or BMoE promotion.
