# ACR-20260924-openclaw-finalizer-environment

- `acr_id`: `ACR-20260924-openclaw-finalizer-environment`
- `title`: Share native worker environment construction with post-retirement finalizer
- `author`: BreadBoard E4 OpenClaw implementation
- `date`: 2026-09-24
- `status`: implemented (local scoped proof; independent exact-head review and installed Linux replay required)

## 1) Problem Statement

Commit `317366ca` added post-retirement native finalization (`invoke_native_finalization_phase` in `sandbox.py`), launching the native worker with `env={}`. The main native session constructs its environment in `sandbox.py` using `plan.runtime.fixed_environment` plus per-adapter settings (`LD_LIBRARY_PATH`, `OPENCLAW_DIST`, etc.). In the installed SIF container on DO-2 (job 1218), running with empty environment caused node to lose `LD_LIBRARY_PATH` and fail to load `libatomic.so.1`, exiting with code 127 and failing all installed cases at turn 0.

## 2) Scope and Surfaces

- Implementation: `breadboard/rl/harness/sandbox.py` (`_native_worker_environment` extracted and applied at both session launch and finalizer launch).
- Tests: `tests/rl/harness/test_sandbox_native_phase_admission.py` (`test_openclaw_finalizer_launch_receives_native_session_environment`).
- Kernel danger-zone: yes, `breadboard/rl/harness/sandbox.py` is in the kernel protected surface.

## 3) Coupling and Generalization Impact

- Reuses exactly the existing session environment logic via `_native_worker_environment(plan, binding) -> dict[str, str]`.
- No behavioral change for the main native session launch path.
- In finalization launch, preserves all `plan.runtime.fixed_environment` entries (including `LD_LIBRARY_PATH` and `PATH`) and per-adapter variables (`OPENCLAW_DIST` for OpenClaw, `PI_CODING_AGENT_NODE_MODULES` and `PI_NATIVE_WORKER_FRAMED` for Pi).
- Other finalizer launch parameters:
  - `cwd`: remains `binding.runtime_root_path`, isolating the finalizer from workspace mutation.
  - `pass_fds`: retains only `node.fd`, as the finalizer operates after workspace cleanup without shell/command descriptors.
  - `argv`: identical interpreter flags and imports via `_native_worker_argv`, plus `--finalize-only`.

## 4) Change Classification

- Classification: `behavioral-change` (finalizer subprocess receives the full admitted native worker environment instead of `{}`).
- Compatibility window: none.
- Schema bump: none.

## 5) Evidence and Validation Plan

- Unit test `test_openclaw_finalizer_launch_receives_native_session_environment` in `test_sandbox_native_phase_admission.py`:
  - Captures environment passed to both session launch and finalizer launch.
  - Asserts equality of finalizer environment and session environment, specifically validating `LD_LIBRARY_PATH` and `OPENCLAW_DIST`.
  - Red at base `494dde75` (finalizer received `{}`), green after fix.
- Gated test suites:
  - `tests/rl/harness/test_openclaw_2026_9_4_native_tools.py` (12 passed, 1 skipped)
  - `tests/rl/harness/test_openclaw_2026_9_4_semantics.py` (10 passed)
  - `tests/rl/harness/test_openclaw_native_stream_conductor.py` (16 passed)
  - `tests/e4_parity/test_openclaw_2026_9_4_comparator.py` (22 passed, 7 skipped)
  - `tests/rl/harness/test_sandbox_native_phase_admission.py` (21 passed)
- ACR gate check: `scripts/check_danger_zone_acr.py` passes with 0 errors.
- Kernel contract pack: `scripts/check_kernel_contract_pack_v1.py` passes with 0 errors.

## 6) Rollout Plan

Independent review must inspect the shared environment helper and test on the exact commit. DO-2 replay retry with the updated image will verify that the node executable successfully loads `libatomic.so.1` in the SIF container.

## 7) Rollback Plan

Revert the commit if any environment variable leakage or regression occurs.

## 8) Approvals

- Kernel reviewer: independent exact-head review required.
- Contracts reviewer: independent exact-head review required.
- Final decision: Main retains promotion authority; this ACR does not authorize merge.
