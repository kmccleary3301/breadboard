# ACR-20260925-openclaw-main-merge

- `acr_id`: `ACR-20260925-openclaw-main-merge`
- `title`: OpenClaw native stream after merging main 1849de68 (Hermes checkpointed phases, compiled required lists, declared workspace)
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-25
- `status`: implemented

## 1) Problem Statement

Main 1849de68 (PR #138, Hermes) reshaped the Conductor's native loop into typed phase steps (`streaming` and `checkpointed`), made the worker-target lowering record a `required` list on every tool, and added `declared_workspace` to `NativeSourceSessionPort`. The OpenClaw lane had its own changes in the same code: result classification and finalization phases, the recorded termination, and a projection that left out an empty `required` list so the compiled tools matched OpenClaw's wire form.

A textual merge gives two failures:
- The binding's `tool_surface_digest` is now computed over tools that carry `required: []`, but the lane's projection still dropped that key. Every OpenClaw target fails with "compiled target outputs differ from their source binding".
- The worker's bootstrap tools are in OpenClaw's pinned wire form, which has no empty `required`. They no longer match the compiled `chat_tools`, so `bind_native_stream` fails.

## 2) Scope and Surfaces

- Kernel modules touched (merge resolution only):
  - `breadboard/rl/harness/runners/conductor.py`: OpenClaw's classification, finalization and recorded termination now run inside main's streaming phase-step body.
  - `breadboard/rl/harness/native_stream_profiles.py`: union of both sides' typed fields; OpenClaw declares `classify_result_phase` and `finalize_result_phase`.
  - `breadboard/rl/harness/policy_provider.py`: see section 3.
  - `breadboard/rl/harness/sandbox.py`: OpenClaw's adapter id joins main's adapter registry. The launch environment stays in one helper, `_native_worker_environment`, which now also sets main's Hermes `PYTHONEXECUTABLE`.
  - `breadboard/product/harness/targets.py`: the OpenClaw recipe joins main's recipe table.
  - `breadboard_engine/compilation/provider_response.py` and `breadboard_engine/provider/profiles.py`: union of both sides' consumer ids and request-policy fields.
- Tests: `tests/rl/harness/test_openclaw_native_stream_conductor.py` (the test port declares its workspace); union resolutions in `tests/rl/harness/test_runner_conductor.py`, `tests/e4_parity/test_comparator_rerun_semantics.py` and `tests/test_breadboard_cli_packaging.py`.
- Contract surfaces touched: OpenClaw native stream bootstrap and request admission.
- Kernel danger-zone: yes, the native-stream Conductor loop and the native provider binding shared by Pi, Hermes, OpenHands and OpenClaw.

## 3) Coupling and Generalization Impact

- Core -> extension dependency: no. The Conductor names no profile.
- The compiled tool projection now follows main for every target: it always carries `required`. The lane's `omit_empty_required` flag is removed.
- A single helper, `_openclaw_wire_tools`, maps compiled tools to OpenClaw's pinned wire form by deleting an empty `required` list. It is used in two places:
  - `bind_native_stream`, for the bootstrap tools;
  - `_responses_request_to_chat`, for request tools, replacing the lane's inline copy of the same deletion.
- Only the OpenClaw consumer uses the helper. The Pi, Hermes and OpenHands comparisons are byte-identical to main.

## 4) Change Classification

- Classification: `internal` (merge resolution; no new behavior for any consumer).
- Required schema/version bumps: none.

## 5) Evidence and Validation Plan

- `tests/rl/harness/test_openclaw_native_stream_conductor.py`: 18 passed. Before the helper, 7 failed with the bootstrap binding error and, before that, 10 failed with the output-digest error.
- Passing and unchanged:
  - `tests/rl/harness/test_policy_provider.py`
  - `tests/rl/harness/test_pi_native_stream_conductor.py`
  - `tests/rl/harness/test_runner_conductor.py`
  - the Hermes and OpenHands conductor and comparator suites
  - `tests/test_e4_targets.py`, whose pinned main lowering digests are unchanged
- OpenClaw installed replay on DO-2 at the merged head is required before live qualification.

## 6) Rollout Plan

- Rollout phases: merge commit on `e4/openclaw-20260924`, PR #140.
- Flags/toggles: none.
- Blast radius constraints: the OpenClaw native stream; every other consumer follows main unchanged.
- Monitoring hooks: the OpenClaw conductor tests and the OpenClaw installed replay.

## 7) Rollback Plan

- Trigger conditions: an OpenClaw bootstrap or request that matches its compiled tools is rejected, or one that differs is admitted.
- Exact rollback commands: `git revert -m 1 <merge commit>`.
- Artifact/state restoration steps: none.
- Post-rollback verification: rerun `tests/rl/harness/test_openclaw_native_stream_conductor.py`, `tests/rl/harness/test_policy_provider.py` and both contract guards.

## 8) Approvals

- Kernel reviewer: Main (campaign orchestrator) under standing approval
- Contracts reviewer: Main (campaign orchestrator)
- Ops reviewer: Main (campaign orchestrator)
- Final decision: Main (campaign orchestrator)
