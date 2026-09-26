# ACR-20260927-pi-0-57-1-historical-clone

- `acr_id`: `ACR-20260927-pi-0-57-1-historical-clone`
- `title`: Source-derived historical consumer for pinned `@mariozechner/pi-coding-agent@0.57.1`
- `author`: Kyle McCleary
- `date`: 2026-09-27
- `status`: implemented on branch `e4/historical-pi057-r2-20260927` (base `eaa92dedeb0c4e0a74ffa8a478cb4119d16b0103`); independent review, CI, and protected merge pending

## 1) Problem Statement

ENGINE_STOP_AND_PR_8-27_REQUEST.md RL-E4-2 (lines 526-550) and RL-PROVIDER-1 (lines 552-572) require a BreadBoard consumer for historical `pi@0.57.1` that runs inside BreadBoard's own Conductor loop and reproduces the pinned harness's model-visible behavior and provider request body. The only existing 0.57.1 surface is the pinned v1 recipe package `config/e4_targets/pi/0.57.1/**` with its legacy string-template lowering, plus a dead one-shot worker, `breadboard/rl/harness/pi_tools.mjs`. That worker had no caller and loaded pi-coding-agent through a bare `import()`. No native-stream consumer, framed worker, semantics state, or provider-body contract existed for 0.57.1.

The parity target is the `declared__*` runs of the r6 declared capture packet (one attempt, no fallback). The packet is `docs_tmp/bb_direction_assessment/engine_pr_handoff_20260827/e4_admission_20260914T221653Z/do2-20260923/hist-pi057/r6/x/packet`, SHA-256 `14527fc6370808897647861cf2da09e9e7c29e48a233d517211105b8d17799a4`. The `p*` (r3_readonly) runs record R3's accidental retry-enabled behavior. They are historical facts, not the clone target. Upstream processes serve only as oracles.

## 2) Scope and Surfaces

Kernel danger-zone change? yes

The sites come from the accepted site-to-decision table. No LSP server was available on this host (the `lsp` tool reported "No language servers configured for this project", and the venv and PATH have no pyright, jedi, or pylsp). The table was built instead from a repo-wide `grep`, with gitignore off, for `PI_RESPONSE_CONSUMER_ID`, `PI_CODING_AGENT_LOCAL_ADAPTER_ID`, `"breadboard.pi-coding-agent.v0.73.1"`, `PI_NATIVE_TOOL_IDS`, `"pi-coding-agent.local.v0.73.1"`, and `pi_tools.mjs`. A later reviewer should repeat the reference search with an LSP once one is configured.

Existing surfaces changed (site number → decision):

- `breadboard_engine/compilation/provider_response.py` (1-3): adds the sibling constant `PI_0_57_1_RESPONSE_CONSUMER_ID = "breadboard.pi-coding-agent.v0.57.1"`, a streaming entry in `_NATIVE_RESPONSE_CONSUMER_MODES`, and a parallel binding check for target `pi-r3@0.57.1` (target_version 3, streaming, `max_tokens` field). The 0.73.1 branch is unchanged.
- `breadboard_engine/provider/runtimes/openai/chat.py` (4-6): adds the 0.57.1 consumer to the pass-through messages/tools sets and pops `n`. It does not set `store`, because custody compat is `supportsStore=false` and pinned `buildParams` (`openai-completions.js:307-309`) omits it.
- `breadboard/rl/harness/policy_provider.py` (7-13): adds the sibling import, the consumer→target map entry, the `n` discard, the native-consumer and streaming-renderer set memberships, and a separate `public_config` branch built from `runtime_profile.model_registry`. That branch uses no `.get` defaults and fails closed on missing keys. The 0.73.1/OpenClaw branch is untouched.
- `breadboard/rl/harness/policy_provider.py` (tool projection): the compiled projection always records a `required` list (`targets.py` `_lower_worker_target`). Pinned 0.57.1 TypeBox `Type.Object` omits `required` when every member is optional (`ls`, pi-coding-agent `dist/core/tools/ls.js`; the r6 captured wire `ls` schema has no `required`). This is the same behavior OpenClaw's tool builder has. The OpenClaw-only helper `_openclaw_wire_tools` is renamed `_empty_required_omitted_tools`, and the set `_EMPTY_REQUIRED_OMITTED_TARGETS = {OpenClaw, Pi 0.57.1}` selects it in both native tool comparisons (`bind_native_stream` and `_responses_request_to_chat`). This supersedes ACR-20260925-openclaw-main-merge §33-37's statement that only OpenClaw uses the helper. OpenClaw output is byte-identical. Every other consumer still compares against the compiled projection as-is.
- `breadboard/rl/harness/native_stream_profiles.py` (14): adds a new `NativeStreamProfile` entry keyed by the 0.57.1 consumer and two optional fields (see §3).
- `breadboard/rl/harness/sandbox.py` (15-18): adds `PI_0_57_1_LOCAL_ADAPTER_ID = "pi-coding-agent.local.v0.57.1"`, `PI_0_57_1_NATIVE_TOOL_IDS = ("bash","edit","find","grep","ls","read","write")`, the `NATIVE_PHASE_TOOL_IDS` entry, and the worker-env branch (`PI_NATIVE_WORKER_FRAMED=1`, `PI_CODING_AGENT_NODE_MODULES=<runtime_root>/node_modules`, `PI_OFFLINE=1`). The new names are exported in `__all__`.
- `breadboard/rl/harness/runners/conductor.py`: optional-phase dispatch for `provider_failure_phase` and `parse_arguments_phase`, plus the `request_body` equality guard (`RunnerProtocolError(code="native_request_body_mismatch")`). It contains no profile names and no `target_id ==` branches.
- `breadboard/product/harness/targets.py` (20-21): adds a `_NATIVE_WORKER_RECIPES` entry for `pi-r3@0.57.1` and the 0.57.1 worker selector.
- `config/e4_targets/index.json`: adds an entry for the new v2 package.
- `pyproject.toml` (24): package-data adds `pi_tools_0_57_1.mjs` and removes `pi_tools.mjs`.
- `breadboard/rl/harness/pi_tools.mjs` (25): deleted. A repo-wide grep (gitignore off, excluding `docs_tmp`) found references only in `pyproject.toml` and in the historical prose of `ACR-20260917-contained-pi-headless.md`.
- Sites with no edit: `breadboard/rl/harness/composition.py` (19, gets the new entry through `NATIVE_PHASE_TOOL_IDS`), `config/e4_targets/pi/0.73.1/harness.yaml` (22), `breadboard/rl/harness/pi_native_tools.py` (23; the host parse path is unchanged for 0.73.1), and the 0.73.1 tests `tests/rl/harness/test_pi_native_stream_conductor.py`, `tests/rl/harness/test_runner_conductor.py`, `tests/rl/harness/test_sandbox_runtime.py`, and `tests/test_breadboard_cli_packaging.py` (26, 27, 29, 30; the packaging test's package-data assertions never listed `pi_tools.mjs`). `tests/rl/harness/test_sandbox_native_phase_admission.py` (28) keeps its 0.73.1 cases; 0.57.1 env and admission cases are added next to them.
- `.github/workflows/pi-native-stream.yml` (31): unchanged. The sibling workflow below is added instead.

New surfaces:

- `breadboard/rl/harness/pi_tools_0_57_1.mjs`: the framed worker (`initialize`, `project_request`, `parse_streaming_json_batch`, `prepare_tools`, `execute_batch`, `project_provider_failure`, `close`). It imports only through `PI_CODING_AGENT_NODE_MODULES` and uses the same frame protocol, error envelopes, and close contract as `pi_tools_0_73_1.mjs`.
- `breadboard/rl/harness/runners/pi_0_57_1_semantics.py`: `Pi0571SemanticsState`, phase schema `bb.pi-native.v0.57.1`, and trace schema `bb.e4.pi-replay-trace.v1` with profile `pi` and version `0.57.1`.
- `config/e4_targets/pi/0.57.1-r3/` (`target.json`, `harness.yaml`, `prompts/system-prompt.md`, `native-config.json`, `tool-surface.json`, `LICENSE.txt`, `source.json`): target `pi-r3@0.57.1`, serialized with `serialize_e4_target`, with overlay `breadboard.pi.r3-headless.v0.57.1`.
- `conformance/comparators/pi_coding_agent_0_57_1.py`, its registry entry, and `tests/e4_parity/fixtures/pi_0_57_1_supplier_cases/` (copied from the r6 declared cases, with a manifest that binds the packet SHA-256, member paths, and file SHA-256).
- `tests/fixtures/pi_0_57_1_node/{package.json,package-lock.json}`: the R3 custody lock (`@mariozechner/pi-coding-agent` integrity `sha512-u5MQEduj68rwVIsRsqrWkJYiJCyPph/a6bMoJAQKo1sb+Pc17Y/ojwa+wGssnUMjEB38AQKofWTVe8NFEpSWNw==`).
- `.github/workflows/pi-0-57-1-historical.yml`: runs `npm ci --ignore-scripts` on the pinned lock, verifies the installed version, lock integrity, and registry URL, then runs `tests/rl/harness/test_pi_0_57_1_*.py` and `tests/e4_parity/test_pi_0_57_1_*.py` with `PI057_CODING_AGENT_NODE_MODULES` set and `BB_REQUIRE_PINNED_PI057_NODE=1`.
- Tests: `tests/rl/harness/test_pi_0_57_1_*.py` and `tests/e4_parity/test_pi_0_57_1_*.py`.

Outside scope: `config/e4_targets/pi/0.57.1/**` and its index entry (SHA-256 `0131e41b0ffbbd0800fd332b769de2603caf4716382989a50c9e47a8f8d2441c`) stay byte-identical, as do `config/e4_targets/oh_my_pi/16.2.13/**` and its entry. Also outside scope: the legacy string-template lowering, and the 0.73.1, OMP, OpenClaw, and Hermes behavior.

## 3) Coupling and Generalization Impact

The 0.57.1 consumer is a sibling of the 0.73.1 consumer. It has its own identity constants, profile entry, worker, semantics module, and target package. It introduces no aliases, no version flags inside `pi_semantics.py`, and no `target_id ==` branches. The Conductor stays one loop driven by profile-declared phases.

`NativeStreamProfile` gains two optional fields, both defaulting to `None`:

- `provider_failure_phase: str | None = None`. When set, an HTTP-status provider failure (`ProviderRuntimeError` details `{"code":"provider_http_status","http_status":int,"response_body_text":str}`, found through `_find_native_provider_failure`) is projected by the sandboxed worker. The worker uses pinned openai `APIError.generate` inside pinned `streamSimpleOpenAICompletions`, so the assistant error message comes from pinned code. Python holds no hard-coded provider error strings. OpenClaw leaves the field `None` and keeps `str(failure)`.
- `parse_arguments_phase: str | None = None`. When set (0.57.1: `"parse_streaming_json_batch"`), the Conductor parses streamed tool arguments on the sandboxed framed worker. When `None`, the existing host path through `pi_native_tools.py` is unchanged for 0.73.1. No host env points at the 0.57.1 node root.

Every existing profile leaves both fields `None`, so the 0.73.1, OMP, OpenClaw, and Hermes code paths are unchanged. The new `request_body` guard fires only when a worker returns `request_body`, and only the 0.57.1 worker does. The 0.57.1 profile uses api_variant `chat_completions`, provider `vllm-local` from the custody model registry, and `supportsDeveloperRole` false. It never uses api_variant `responses` or provider `openai`. The node root comes only from `PI_CODING_AGENT_NODE_MODULES` (runtime) or `PI057_CODING_AGENT_NODE_MODULES` (tests), with no filesystem discovery. Coupling risk is medium: the Conductor phase dispatch and the provider-failure chain are shared code, guarded by `None` defaults.

## 4) Change Classification

- Classification: `additive`

The change adds a new target, consumer, worker, semantics state, comparator, CI lane, and optional profile fields with `None` defaults. Existing constructors, profiles, and target identities keep their behavior. Deleting `breadboard/rl/harness/pi_tools.mjs` removes a packaged file that no code, test, or config referenced.

## 5) Evidence and Validation Plan

- Parity oracle: r6 declared packet SHA-256 `14527fc6370808897647861cf2da09e9e7c29e48a233d517211105b8d17799a4`. The fixture manifest binds each copied member path and file SHA-256 to that packet.
- The pinned node root is installed from `tests/fixtures/pi_0_57_1_node` with `npm ci --ignore-scripts`. Its pi-coding-agent, pi-ai, and pi-agent-core dist trees and its openai package are byte-identical to R3 custody (`diff -rq`), at versions pi-coding-agent/pi-ai/pi-agent-core 0.57.1 and openai 6.26.0.
- Conductor tests: exact request-body equality (`native_request_body_mismatch`), provider-failure projection through pinned code, sequential tool execution, request-cap termination, `native_unmodeled_finish_reason`, and rejection of truncated streams.
- The comparator runs `agent_end.messages` against the trace messages, normalizing only `timestamp`, and also compares request bodies, request counts, tool calls, observations, effects, and termination for every declared case. The typed rules are `workspace_root`, `home_root`, `package_dir_documentation_path`, `request_limit_cause`, and `current_date_time_system_line`.
- Named divergences are reported and never normalized:
  - `sdk_hidden_transport_retry`: upstream openai@6.26.0 silently retries 408/409/429/5xx twice; BB sends exactly one request (P5 BB 2 vs 4, P7 BB 1 vs 3, P8 BB 1 plus an error terminal vs SDK recovery).
  - `bounded_request_cap`: request cap 8; the unsent ninth attempt raises `RequestLimitExceeded`. Upstream is unbounded.
  - `tool_download_attempt`: `PI_OFFLINE=1`; the find/grep error text is the same as the capture.
  - `compaction_start_then_exit`: upstream emits `auto_compaction_start` after `agent_end` in print mode. It sends no summary request and has no model-visible effect.
  - `sdk_transport_headers`.
  - `json_member_order`.
  - `truncated_stream_rejected`: `accepts_truncated_stream=False`. Pinned `openai-completions.js:45` keeps `"stop"` when finish_reason is absent.
  - `unmodeled_finish_reason`: finish_reason outside the pinned `mapStopReason` set (`:616-634`) raises a typed `RunnerProtocolError`.
  - `non_http_transport_failure`: a connection or mid-stream transport failure without `http_status` is a typed failure and is not projected.
  - `external_cancel_signal`: P4. The BB replay sends SIGINT at the anchor. The prefix through the anchor is exact, and BB terminates with failed / CancelledError / exit 130 at the same request count.
- CI: `.github/workflows/pi-0-57-1-historical.yml` must pass with `BB_REQUIRE_PINNED_PI057_NODE=1`, so a missing node root fails instead of skipping. The danger-zone ACR guard (`scripts/check_danger_zone_acr.py`) must pass on the changed-file list from `eaa92ded..HEAD`.
- Site enumeration is grep-based because no LSP is available on this host (§2). It is recorded as the accepted enumeration.
- No installed DO-2 replay or external acceptance is claimed.

## 6) Rollback Plan

If a 0.57.1 comparison diverges outside the named divergences, the request-body guard misfires, or an existing profile's behavior changes, revert the 0.57.1 change through the protected review path. The pinned v1 `pi@0.57.1` package and the 0.73.1 lane are untouched, so reverting removes only sibling surfaces and the two optional fields. Do not re-add `pi_tools.mjs` as a fallback. Rerun the pinned Pi lanes and the danger-zone ACR guard on the rollback candidate.

## 7) Approvals

This ACR records the design decisions D1-D10 and the accepted grep-based site table. Independent review of the exact head, applicable CI, and the protected merge decision are pending. Main and the campaign owner retain those decisions. This document grants no merge authority and no DO-2 qualification.
