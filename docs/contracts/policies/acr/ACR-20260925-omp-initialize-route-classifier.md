# ACR-20260925-omp-initialize-route-classifier

- `acr_id`: `ACR-20260925-omp-initialize-route-classifier`
- `title`: Conductor passes the compiled OMP route classifier to the native worker's initialize phase
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-25
- `status`: implemented

## 1) Problem Statement

The pinned OMP worker (`omp_native_tool_worker.ts`, `initialize`) requires `payload.route_classifier`. The worker uses it to verify the pinned source root, module digests and `bun.lock` before it loads any source module. The compiled OMP runtime profile already carries the sealed `route_classifier` from `config/e4_targets/oh_my_pi/18.1.17/native-config.json`. However, the conductor's native-stream initialize payload sent only `task`, `model_config`, `advertisement` and `runtime_inputs`.

In-repo tests did not notice, because the test-only `omp_native_tools.NativeToolWorker.phase` injects the repository classifier when it is missing. The installed runtime has no such injection. On DO-2, every OMP composition-path case failed at turn 0 with `WorkerPhaseError: route_classifier must be an object` (job 1360, head 505f03f3, SIF 8e18bcf9).

## 2) Scope and Surfaces

- Kernel modules touched:
  - `breadboard/rl/harness/native_stream_profiles.py`: OMP declares `sealed_initialize_fields=("route_classifier",)`. Main (#138) introduced that typed field for the checkpointed Hermes phase mode; the lane's own `initialize_profile_fields` was removed in the merge.
  - `breadboard/rl/harness/runners/conductor.py`: the native-stream initialize payload.
- Tests: `tests/rl/harness/test_omp_native_initialize_payload.py`.
- Contract surfaces touched: the native-stream initialize phase payload.
- Kernel danger-zone: yes, the conductor's native worker phase protocol.

## 3) Coupling and Generalization Impact

- Core -> extension dependency: no. The conductor names no profile. The streaming initialize payload now carries each field the profile declares in `sealed_initialize_fields`, copied verbatim from the compiled runtime profile, as the checkpointed initialize already did.
- The loop's admission check fails closed with `compiled_ir_mismatch` when a declared field is not an object in the compiled profile.
- Pi declares no fields, so its payload is byte-identical to before.

## 4) Change Classification

- Classification: `internal` (bug fix: the conductor forwards an already-compiled sealed value).
- Required schema/version bumps: none. The compiled manifest and target digests are unchanged; the value was already compiled.

## 5) Evidence and Validation Plan

- `tests/rl/harness/test_omp_native_initialize_payload.py` compiles the real `oh-my-pi@18.1.17` target and runs the conductor against an installed-shape port. The port does no injection, and it records the initialize payload.
  - The test asserts that `route_classifier` equals the pinned native-config value.
  - It fails at base 505f03f3 (`KeyError: 'route_classifier'`).
- The conductor, Pi native-stream, OMP semantics, worker, target-asset and rerun5 replay suites stay green.
- DO-2 installed replay of the six OMP cases at the new head.

## 6) Rollout Plan

- Rollout phases: commit on `e4/omp-complete-20260923`, PR #136.
- Flags/toggles: none.
- Blast radius constraints: the OMP native-stream initialize payload only.
- Monitoring hooks: the OMP installed replay and comparator.

## 7) Rollback Plan

- Trigger conditions: the OMP worker rejects the classifier, or a non-OMP native initialize payload changes.
- Exact rollback commands: `git revert <commit>`.
- Artifact/state restoration steps: none.
- Post-rollback verification: rerun `tests/rl/harness/test_runner_conductor.py`, `tests/rl/harness/test_pi_native_stream_conductor.py` and both contract guards.

## 8) Approvals

- Kernel reviewer: Main (campaign orchestrator) under standing approval
- Contracts reviewer: Main (campaign orchestrator)
- Ops reviewer: Main (campaign orchestrator)
- Final decision: Main (campaign orchestrator)
