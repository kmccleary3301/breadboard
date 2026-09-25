# ACR-20260925-openclaw-omp-main-merge

- `acr_id`: `ACR-20260925-openclaw-omp-main-merge`
- `title`: OpenClaw lane after merging main 38093550 (OMP 18.1.17, PR #136)
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-25
- `status`: implemented

## 1) Problem Statement

Main 38093550 (PR #136) added the OMP 18.1.17 native profile in the same code as the OpenClaw lane:
- the native-stream profile table;
- the sandbox adapter registry and worker launch environment;
- the native chat request builder;
- the policy provider's native-stream admission;
- the Conductor's compiled schema admission.

All thirteen conflicts are additive. Each side added its own consumer to the same sets, tables or branches.

## 2) Scope and Surfaces

- Kernel modules touched (merge resolution only):
  - `breadboard/rl/harness/sandbox.py`:
    - Registry: both adapter ids are admitted.
    - `_native_worker_environment` is still the one helper for every native worker launch. It now uses main's explicit per-adapter branches:
      - Pi;
      - OMP (no additions);
      - OpenClaw;
      - OpenHands and Hermes;
      - any other adapter fails closed with `runtime_unsupported`.
    - The helper takes `lease_id`, so its failures carry the lease, as main's inline code did.
  - `breadboard/rl/harness/native_stream_profiles.py`: union of the typed fields (`accepts_truncated_stream`, `provider_failure_terminates`) and of the two profiles.
  - `breadboard/rl/harness/policy_provider.py`: consumer sets are unioned. `bind_native_stream` keeps OpenClaw's wire-form tool comparison and main's `_native_binding` and `accept_truncated_stream` checks. Source-native request admission checks OpenClaw's bound-prompt branch first, then main's generalized deferred-prompt branch (v3 with no rendered digest; it would otherwise also catch OpenClaw, whose wire system message relocates the runtime line), then main's rejection of a bound prompt for any other target. Without that order, 8 OpenClaw conductor tests fail with "source-native request does not match its compiled source surface".
  - `breadboard_engine/provider/runtimes/openai/chat.py`:
    - The native stream call passes main's `accept_truncated_stream` inside the lane's `NativeProviderRequestFailure` wrapper.
    - Pi, OMP and OpenClaw omit `n`. Only Pi and OMP set `store: false`, as the pinned OpenClaw builder emits no `store`.
  - `breadboard/rl/harness/runners/conductor.py`: `_admit_schema` keeps the lane's `patternProperties` admission and main's depth/node budget and `additionalProperties` schema admission. Pattern-property children are now also admitted under that budget.
  - `breadboard/rl/harness/native_stream_profiles.py` also gets main's now-required `api_variant`. OpenClaw declares `responses`, the value the lane's Conductor expected for a consumer outside `NATIVE_CHAT_RESPONSE_TARGETS`.
  - `breadboard/product/harness/targets.py`, `pyproject.toml`, `conformance/comparators/registry.json`: both profiles' entries.
- Tests (union resolutions):
  - `tests/test_e4_targets.py`, `tests/test_breadboard_cli_packaging.py`, `tests/e4_parity/test_comparator_rerun_semantics.py` and `tests/rl/harness/test_sandbox_native_phase_admission.py`.
  - `tests/test_e4_targets.py` gets back the `def test_pinned_targets_load_with_exact_release_source_and_runtime_assets` line. Main lost it in 0f451004, so that test's body had been running inside the symlink test.
- Contract surfaces touched: none beyond the union.
- Kernel danger-zone: yes, the native-stream Conductor schema admission, the native provider binding, and the trusted-process native worker environment shared by every native profile.

## 3) Coupling and Generalization Impact

- Core -> extension dependency: no. The Conductor branches only on typed profile fields.
- Pi, OMP, OpenHands and Hermes behave as on main. OpenClaw behaves as on the lane.

## 4) Change Classification

- Classification: `internal` (merge resolution).
- Required schema/version bumps: none.

## 5) Evidence and Validation Plan

- After the merge:
  - the OpenClaw, OMP, Pi, policy provider, runner Conductor, sandbox admission, target and packaging suites pass;
  - the comparator registry semantics suite passes.
- OpenClaw installed replay on DO-2 at the merged head is required before merge.

## 6) Rollout Plan

- Rollout phases: merge commit on `e4/openclaw-20260924`, PR #140.
- Flags/toggles: none.
- Blast radius constraints: every native-stream profile; resolved as a union.
- Monitoring hooks: the suites above and the OpenClaw installed replay.

## 7) Rollback Plan

- Trigger conditions: a native profile's launch environment, request builder or schema admission differs from main (for non-OpenClaw profiles) or from the lane (for OpenClaw).
- Exact rollback commands: `git revert -m 1 <merge commit>`.
- Artifact/state restoration steps: none.
- Post-rollback verification: rerun the suites above and both contract guards.

## 8) Approvals

- Kernel reviewer: Main (campaign orchestrator) under standing approval
- Contracts reviewer: Main (campaign orchestrator)
- Ops reviewer: Main (campaign orchestrator)
- Final decision: Main (campaign orchestrator)
