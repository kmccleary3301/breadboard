# ACR-20260925-envelope-stacked-mount-view

- `acr_id`: `ACR-20260925-envelope-stacked-mount-view`
- `title`: Verify only visible mounts as lease containment roots
- `author`: BreadBoard E4 containment lane
- `date`: 2026-09-25
- `status`: implemented; independent exact-head review pending

## 1) Problem Statement

Inside `apptainer exec --containall`, `/tmp` is already a mount. The lease envelope remounts `/` read-only, recursively, then mounts its lease tmpfs over `/tmp`. The covered runtime `/tmp` entry stays in `/proc/self/mountinfo`. `_verify_mount_view` treated every line whose mount point is a lease root as that root, so the covered read-only `/tmp` failed the writable check with EROFS. Every trusted-process lease in an Apptainer SIF then failed with `envelope_launch_failed` before the agent started (DO-2 job 1273). The bare-host containment gate (job 1196) passed only because `/tmp` is not a mount there.

## 2) Scope and Surfaces

- `breadboard.rl.harness.lease_envelope`: mount-view verification only.
- Process-integration tests for stacked, covered-writable and duplicate-visible lease roots.
- Danger-zone: yes. No change to the mount setup sequence, receipt schema, mountinfo digest, isolation promotion, privileges, kernel settings, model-visible capabilities or provider behavior.

## 3) Coupling and Generalization Impact

A mountinfo entry is covered when another entry names it as parent on the same mount point. Root checks (rw, uniqueness, tmpfs type, size bound, nosuid/nodev) apply only to the visible entry for each lease root. Every covered entry, including a covered root path, must pass the inherited-mount check and be read-only, otherwise the lease fails closed. The envelope does not unmount the runtime `/tmp`: in user-namespace mode inherited mounts are locked, so an unmount would work in one mode only. The receipt digest stays over the raw mountinfo bytes.

## 4) Change Classification

- Classification: `additive`.

Corrective, fail-closed change. Two visible mounts for one root and a writable covered mount are both still rejected. No persistence schema or public plan change.

## 5) Evidence and Validation Plan

Focused macOS tests: the stacked layout from Apptainer is accepted; a covered writable mount and two sibling mounts for one root are rejected. Before the fix, the stacked-layout test reproduces the production EROFS. On DO-2, launch the envelope inside the OpenHands SIF with the replay's exact Apptainer flags. Show the pre-existing `/tmp` mount, a successful attested launch, write confinement (workspace and `/tmp` writable, `/` and `/opt` EROFS), and a `setsid` escapee killed at teardown. The unpatched module runs in the same job as a control. Run the danger-zone ACR guard and the kernel contract pack.

## 6) Rollout Plan

Require exact-head independent correctness/security review and green CI before protected merge. Then merge main into the open profile lanes and rerun their installed replays under the envelope.

## 7) Rollback Plan

Revert this commit through normal protected review if covered-mount classification admits a writable inherited mount or a duplicate root. No migration is required.

## 8) Approvals

Independent exact-head containment reviewer required. This document grants no merge authority.
