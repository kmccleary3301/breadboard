# ACR-20261013-rl-native-run-ceilings

- `acr_id`: `ACR-20261013-rl-native-run-ceilings`
- `title`: RL native targets admit the plan's request and wall budget up to the target's verified ceiling
- `author`: BreadBoard compaction implementation
- `date`: 2026-10-13
- `status`: implemented

## 1) Problem Statement

Native stream profiles pin the stock capture budget: 8 requests, a 120 s episode, and the profile's tool timeout. The Conductor requires the plan's `max_turns` to equal that request cap exactly. An RL eval or training episode that runs long enough to compact needs more than 8 requests. Under the kit cap, the step-5 RL smoke (eval 177153) stopped every episode before compaction could trigger.

## 2) Scope and Surfaces

- Kernel danger-zone: yes. The change touches the `breadboard/rl/harness/**` Conductor, native stream profiles and the Hermes worker; `breadboard/product/harness/targets.py` lowering; and `breadboard_engine/compilation/provider_response.py`.
- New RL target revisions, copied from their predecessors. Each adds `"budget_admission": "ceiling"` to `native-config.json`, and sets `bounded_controls.max_model_calls: 100000` and `episode_wall_seconds: 10200` in `harness.yaml`:
  - `pi-r5@0.57.1`
  - `pi-r4@0.73.1`
  - `oh-my-pi-r4@16.2.13`
  - `oh-my-pi-r3@18.1.17`
  - `openclaw-r3@2026.9.4`
  - `hermes-agent-r3@2026.9.11`
- Lowering copies the harness `bounded_controls` into the runtime profile only for a ceiling revision. It refuses any other admission mode, a native config that supplies its own controls, and non-positive controls.
- When an episode opens, `admit_run_limits` admits `0 < max_turns <= max_model_calls` and `0 < ceil(wall_time_ms / 1000) <= episode_wall_seconds`. It also requires `action_timeout_ms` to equal the profile's tool timeout. The admitted values then set each source's request cap and episode deadline:
  - OpenClaw also scales its tool-admission cap at the kit ratio of 4 per request.
  - Hermes receives `execution_controls` in its sealed `initialize` payload.
- Unchanged: every existing target revision, including the kit targets and the r2–r4 RL revisions, and the E4 target-freeze manifest. Without the marker, the exact-equality check and the worker constants apply as before.

## 3) Coupling and Generalization Impact

- Admission depends on a declared target field, not on the harness's identity. Each profile's state factory receives an optional `NativeRunLimits`; the Hermes worker accepts optional `execution_controls`.
- The plan is still the authority for every run. The ceiling only bounds what the plan may ask for, and the source's own counters still enforce the admitted values.

## 4) Change Classification

- Classification: `additive`. The change adds new target revisions and an opt-in admission mode. Existing revisions compile byte-identically and keep exact equality.

## 5) Evidence and Validation Plan

- Real-object tests:
  - A ceiling plan runs past the kit's request cap.
  - Plans above the ceiling, with a different tool timeout, an unknown mode or no controls fail with `compiled_ir_mismatch` before any provider request.
  - A kit plan that asks for more than its cap is still refused.
  - Hermes receives `execution_controls`, and its worker bounds the wall by them.
  - The OpenClaw state admits requests up to the run's cap.
  - Lowering refuses unknown modes and asset-supplied controls.
  - The installed wheel lowers every successor with the ceiling profile.
- `tests/rl/harness tests/test_e4_targets.py tests/test_breadboard_cli_packaging.py tests/test_runtime_resource_closure.py` fails the same 16 tests at the base `245b1807` and with this change. The two clean-wheel packaging tests need committed build inputs and run after commit.
- Step-5 RL eval and step-6 training canaries on the successor revisions.

## 6) Rollout Plan

- RL launchers select the successor revisions explicitly. Consumers that keep the predecessor IDs see no change.

## 7) Rollback Plan

- Revert the commit. The successors are new IDs, so no existing consumer needs migration.

## 8) Approvals

- This artifact records the implementation decision and its evidence. It does not assert external acceptance or promotion.
