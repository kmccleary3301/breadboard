# ACR-20260925-openclaw-supplier-fidelity

- `acr_id`: `ACR-20260925-openclaw-supplier-fidelity`
- `title`: OpenClaw worker environment, file-tool workspace scope, and provider-failure episodes follow the pinned supplier capture
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-25
- `status`: implemented

## 1) Problem Statement

DO-2 replay job 1355 (kit w96, lane af554ebb) compared BreadBoard's OpenClaw episodes with the admitted supplier packet and found three product defects:

- D-a, skills block: the pinned system prompt listed the bundled plugin skills `browser-automation` and `canvas` and omitted the node-gated skills `meme-maker` and `node-inspect-debugger`. The supplier capture (`kit/openclaw_capture_supplier.py:123`) runs with `OPENCLAW_DISABLE_BUNDLED_PLUGINS=1` and with node's own directory first on `PATH`. The BreadBoard worker had neither, and skill eligibility (`config-eval` `hasBinary`) reads both.
- D-b, write path echo: the worker built the pinned core coding tools with `workspaceOnly: true`, so the pinned guard echoed an absolute path (`Successfully wrote 16 bytes to /replay-out/.../marker.txt`). The supplier's value is `false` (`embedded-agent.runtime-DnOK0ORi.mjs:649`, `agent-tools-DXxcrXNI.mjs:388`; the capture config sets neither), and it echoes the model's own path.
- D-d, provider failure: on the scripted HTTP 500 of `provider_failure_no_retry`, the episode ended `policy_invoke_failed` with no replay trace. The pinned agent exec (retry and model fallbacks off) records the one sent request and ends its run with an error result. The provider layer discarded the sent body (`raise error from None`), and the Conductor had no terminal path for it.

## 2) Scope and Surfaces

- Kernel modules touched:
  - `breadboard/rl/harness/sandbox.py`: `_native_worker_environment` gives the OpenClaw worker `OPENCLAW_DISABLE_BUNDLED_PLUGINS=1` and puts the sealed node's directory first on the runtime `PATH`. A runtime that declares no `PATH` fails closed (`runtime_preflight_failed`).
  - `breadboard_engine/provider/contract_runtime.py`, `contracts.py`: new `NativeProviderRequestFailure(ProviderRuntimeError)`. It carries the exact body of a native request that the provider refused before any output.
  - `breadboard_engine/provider/runtimes/openai/chat.py`: on the native streaming path, a `provider` kind failure with no emitted output is raised as `NativeProviderRequestFailure` with the sent body.
  - `breadboard/rl/harness/policy_provider.py`: that failure is chained (`from exc`), as `MiniProviderFailure` already is. Every other failure keeps `from None`.
  - `breadboard/rl/harness/native_stream_profiles.py`: typed `provider_failure_terminates` field. Only OpenClaw sets it.
  - `breadboard/rl/harness/runners/conductor.py`: when the profile declares `provider_failure_terminates` and the chained failure is present, the streaming step records the sent body, commits the state's provider failure, and ends the step with `POLICY_INCOMPLETE`. Classification, close, effect measurement and finalization then run as for every other terminal.
  - `breadboard/rl/harness/runners/openclaw_semantics.py`: `commit_provider_failure` sets termination `provider_failure` with no native stop reason.
- Non-kernel: `breadboard/rl/harness/openclaw_tool_worker.mjs` (`workspaceOnly: false`).
- Tests: `tests/rl/harness/test_openclaw_2026_9_4_native_tools.py` (relative write echo; skill eligibility under the sealed launch environment) and `tests/rl/harness/test_openclaw_native_stream_conductor.py` (provider failure episode).
- Contract surfaces touched: OpenClaw native worker launch environment, OpenClaw file-tool results, and the native provider failure path.
- Kernel danger-zone: yes, the native-stream Conductor loop, the native provider invocation shared by Pi, Hermes, OpenHands and OpenClaw, and the trusted-process native worker environment.

## 3) Coupling and Generalization Impact

- Core -> extension dependency: no. The Conductor branches on the typed profile field, not on a profile name.
- For every consumer other than OpenClaw:
  - `provider_failure_terminates` is false, so the chained failure is re-raised unchanged.
  - The failure still is a `ProviderRuntimeError` with the same message, kind and details.
  - The launch environment is unchanged.

## 4) Change Classification

- Classification: `additive` (a new typed profile field and a new provider failure subtype; OpenClaw behavior moves to its pinned supplier behavior).
- Required schema/version bumps: none. The target descriptor and native config are unchanged.

## 5) Evidence and Validation Plan

- Each new test fails at base:
  - `test_native_write_echoes_relative_path`: absolute path echoed.
  - `test_native_worker_environment_matches_supplier_skill_eligibility`: `meme-maker` missing.
  - `test_openclaw_provider_failure_ends_episode_with_replay_trace`: `RunnerDependencyError: episode provider invocation failed`.
- Passing: the OpenClaw conductor, native tools, comparator, policy provider, sandbox admission, Pi conductor and runner conductor suites.
- OpenClaw installed replay on DO-2 at the lane head is required before merge.

## 6) Rollout Plan

- Rollout phases: commit on `e4/openclaw-20260924`, PR #140.
- Flags/toggles: `NativeStreamProfile.provider_failure_terminates` (OpenClaw only).
- Blast radius constraints: OpenClaw episodes. Other consumers keep their current failure path.
- Monitoring hooks: the OpenClaw conductor tests and the OpenClaw installed replay.

## 7) Rollback Plan

- Trigger conditions: an OpenClaw provider failure that ends without a replay trace, or a non-OpenClaw provider failure that is no longer raised.
- Exact rollback commands: `git revert <commit>`.
- Artifact/state restoration steps: none.
- Post-rollback verification: rerun the OpenClaw conductor and native tools suites and both contract guards.

## 8) Approvals

- Kernel reviewer: Main (campaign orchestrator) under standing approval
- Contracts reviewer: Main (campaign orchestrator)
- Ops reviewer: Main (campaign orchestrator)
- Final decision: Main (campaign orchestrator)
