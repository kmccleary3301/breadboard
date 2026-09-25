# ACR-20260925-pi-envelope-precreated-home

- `acr_id`: `ACR-20260925-pi-envelope-precreated-home`
- `title`: Pi native worker adopts the lease envelope's pre-created HOME
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-25
- `status`: implemented

## 1) Problem Statement

Pi's installed replay on DO-2 at 6f17ec2f (job 1390) failed all six cases at turn 0 with `terminal.run_failure = runtime/native_worker_remote_error`, and the cleanup disposition was `released`. The failure has three parts:

- `lease_envelope._setup_mount_view` creates `<scratch>/home` (mode 0700) on the lease's private scratch tmpfs.
- `TrustedProcessHandle._start_stopped_process` exports that path as HOME.
- `pi_tools_0_73_1.mjs` initialize then ran a non-recursive `mkdir(resolve(scratch, "home"))` on the same path, and got EEXIST.

The defect has existed since the containment work in #144–#146. Pi's last installed replay (job 1179) predates the envelope, and the Pi conductor tests run the worker without an envelope.

## 2) Scope and Surfaces

- Kernel modules touched: `breadboard/rl/harness/pi_tools_0_73_1.mjs`. Initialize accepts an existing `<scratch>/home` directory (`mkdir(home, { recursive: true })`). `pi-agent` and `tmp` keep the strict mkdir, so they must still be created by the worker.
- Contract surfaces touched: none.
- Kernel danger-zone: yes, a native worker under `breadboard/**`.

## 3) Coupling and Generalization Impact

- Core -> extension dependency: no. The change affects only the Pi worker.
- Other native profiles do not collide with the envelope's home:
  - Hermes uses `<scratch>/hermes-home`.
  - OpenHands uses `exist_ok=True`.
  - OpenClaw takes HOME from runtime inputs.
  - OMP's installed replay passed at 6f17ec2f (job 1383).
- Security: the path sits on a fresh lease-private tmpfs that only the envelope writes before the worker starts. If a non-directory occupied the path, `mkdir` would still fail.

## 4) Change Classification

- Classification: `internal`.
- Required schema/version bumps: none.

## 5) Evidence and Validation Plan

- Test `test_pi_native_worker_adopts_envelope_created_home` pre-creates `scratch/home` the way the envelope does. It fails with EEXIST at base and passes with the fix.
- `tests/rl/harness/test_pi_native_stream_conductor.py`: 26 passed. `tests/e4_parity/test_pi_0_73_1_prompt_materialization.py`: 8 passed.
- Required after merge: the Pi installed replay under the envelope at the new head, plus the other five profiles' replays at that head.

## 6) Rollout Plan

- Rollout phases: branch `fix/pi-envelope-precreated-home`, PR #147.
- Flags/toggles: none.
- Blast radius constraints: Pi native worker initialize only.
- Monitoring hooks: the test above and the Pi installed replay.

## 7) Rollback Plan

- Trigger conditions: a Pi replay difference attributable to HOME handling.
- Exact rollback commands: `git revert <commit>`.
- Artifact/state restoration steps: none.
- Post-rollback verification: rerun the Pi conductor suite.

## 8) Approvals

- Kernel reviewer: Main (campaign orchestrator) under standing approval
- Contracts reviewer: Main (campaign orchestrator)
- Ops reviewer: Main (campaign orchestrator)
- Final decision: Main (campaign orchestrator)
