# ACR-20260925-native-worker-envelope-environment

- `acr_id`: `ACR-20260925-native-worker-envelope-environment`
- `title`: Native worker environment survives the lease envelope
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-25
- `status`: implemented

## 1) Problem Statement

OpenClaw's installed replay on DO-2 at 6583f996 (job 1371) failed every case at turn 0 with `native_worker_remote_error`. Diagnostic jobs 1372, 1373 and 1376 in that image found three launch defects. Each one silently changes the environment the admitted worker sees:

1. **TMPDIR.** The composed runtime declares `TMPDIR=<runtime root>/tmp`. The lease envelope remounts `/` read-only and mounts a private `/tmp`, so that path cannot be created inside the lease. With bundled plugins disabled, OpenClaw creates `os.tmpdir()/openclaw-empty-bundled-plugins`, and the worker failed with ENOENT. Python's `tempfile` falls back to `/tmp`; Node's `os.tmpdir()` and shell `mktemp` do not.
2. **Login shell.** Native workers were started through `sh -lc 'exec "$@"'`. A login shell sources the image's `/etc/profile`, which resets PATH (Debian) or reorders it (macOS path_helper). That dropped the sealed node directory that `_native_worker_environment` puts first. OpenClaw skill eligibility (`hasBinary("node")`) then removed `meme-maker` and `node-inspect-debugger` from the system prompt. The in-image skill-eligibility test spawns the worker directly, so it did not see this.
3. **OOM shim.** OpenClaw's exec shim runs `echo 1000 > /proc/self/oom_score_adj 2>/dev/null` before each child. The envelope mounts `/proc` read-only. The shell reports the failed redirection before `2>/dev/null` takes effect, so `/usr/bin/sh: 1: cannot create /proc/self/oom_score_adj: Read-only file system` replaced the supplier's `(no output)` in `process_exec_effect` and `streaming_fragmented_write`. This difference was already present at af554ebb (w96).

## 2) Scope and Surfaces

- Kernel modules touched:
  - `breadboard/rl/harness/sandbox.py`, `TrustedProcessHandle._start_stopped_process`: every envelope launch sets `TMPDIR=/tmp`, the lease-private tmpfs from `lease_envelope._setup_mount_view`, next to the existing `HOME=<scratch>/home` rewrite. Launches outside an envelope keep the composed value.
  - `breadboard/rl/harness/sandbox.py`, `TrustedProcessHandle.invoke_native_phase`: the native worker wrapper is `sh -c 'exec "$@"'` (not a login shell), so the worker gets exactly the environment `_native_worker_environment` admits. The native finalizer already spawned directly with that environment.
  - `breadboard/rl/harness/sandbox.py`, `_native_worker_environment` OpenClaw branch: `OPENCLAW_CHILD_OOM_SCORE_ADJ=0`, the supplier's own opt-out for the shim.
- Declared deviation (item 3): the supplier's children raise their own OOM score, and replay children do not. Model-visible command output matches the supplier capture, where the write succeeded silently. The alternative was to stop remounting the envelope's `/proc` read-only, which would loosen containment for every profile, so it was rejected.
- Contract surfaces touched: none.
- Kernel danger-zone: yes, the trusted-process launch environment and the native worker launch shared by every native profile.

## 3) Coupling and Generalization Impact

- Core -> extension dependency: no. Items 1 and 2 apply to every envelope launch and native adapter; item 3 is in the existing OpenClaw adapter branch.
- Other native profiles: every composition declares `PATH=/usr/local/bin:/usr/bin:/bin`. Before this change their workers got `/etc/profile`'s PATH, which adds only sbin (or games) directories. Their shells now see `TMPDIR=/tmp` in place of an invisible path. Each profile's native-closure recheck at the final head covers them.

## 4) Change Classification

- Classification: `internal`.
- Required schema/version bumps: none.

## 5) Evidence and Validation Plan

- Diagnostic in the 6583f996 image:
  - Job 1373 applied item 1 through a hook. All six OpenClaw cases ran, and the request diff showed the two missing skills.
  - Job 1376 applied items 1 and 2. Four of six cases were comparator-equal; the other two differed only by the OOM message.
- Tests:
  - `test_envelope_launch_gives_composed_tmpdir_the_lease_tmpfs`: Linux, sealed execution.
  - `test_native_worker_shell_wrapper_keeps_the_admitted_path`: fails at base on macOS because path_helper moves the admitted node directory to the end.
- Required before merge: the full OpenClaw installed replay at the new head.

## 6) Rollout Plan

- Rollout phases: commit on `e4/openclaw-20260924`, PR #140.
- Flags/toggles: none.
- Blast radius constraints: every enveloped launch (TMPDIR) and every native worker launch (non-login shell).
- Monitoring hooks: the tests above, the OpenClaw installed replay, and the per-profile native-closure recheck at the final head.

## 7) Rollback Plan

- Trigger conditions: a native profile's replay or tool output differs because of the worker PATH or TMPDIR.
- Exact rollback commands: `git revert <commit>`.
- Artifact/state restoration steps: none.
- Post-rollback verification: rerun the sandbox admission, process integration and OpenClaw suites.

## 8) Approvals

- Kernel reviewer: Main (campaign orchestrator) under standing approval
- Contracts reviewer: Main (campaign orchestrator)
- Ops reviewer: Main (campaign orchestrator)
- Final decision: Main (campaign orchestrator)
