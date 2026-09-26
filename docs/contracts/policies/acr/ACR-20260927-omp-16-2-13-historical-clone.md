# ACR-20260927-omp-16-2-13-historical-clone

- `acr_id`: `ACR-20260927-omp-16-2-13-historical-clone`
- `title`: Source-derived historical consumer for pinned `@oh-my-pi/pi-coding-agent@16.2.13`
- `author`: Kyle McCleary
- `date`: 2026-09-27
- `status`: implemented on branch `e4/historical-omp16213-20260927`. Independent review, CI and the protected merge are still pending.

## 1) Problem Statement

ENGINE_STOP_AND_PR_8-27_REQUEST.md RL-E4-2 (lines 526-550) and RL-PROVIDER-1 (lines 552-572) require a BreadBoard consumer for historical `oh-my-pi@16.2.13`. It must run inside BreadBoard's own Conductor loop and reproduce, over the applicable subset, the pinned harness's model-visible behavior and provider request body.

The only existing 16.2.13 surface is the pinned v1 recipe package `config/e4_targets/oh_my_pi/16.2.13/**`. Its index entry has SHA-256 `537fb2659e6f8f619e0fd832d48d57c5f8c0da4b2356022b0ebecd6ccbb7a105` and is lowered through the legacy string-template path. No native-stream consumer, framed worker, semantics state or provider-body contract existed for 16.2.13.

The parity target is the `declared__*` runs of the r2 capture packet `docs_tmp/bb_direction_assessment/engine_pr_handoff_20260827/e4_admission_20260914T221653Z/do2-20260923/hist-omp16213/r2/omp16213-capture-packet.tar.gz`, SHA-256 `cf93d5ac54c0271ce90a7028b0c1cde6ff022f7516cb26a838608878e5f54ec9`. Each run had one attempt and no fallback. Upstream processes serve only as oracles.

## 2) Scope and Surfaces

Kernel danger-zone change? yes

The sites were enumerated with a repo-wide `grep`, with gitignore off, for `OMP_RESPONSE_CONSUMER_ID`, `OMP_NATIVE_LOCAL_ADAPTER_ID`, `"breadboard.oh-my-pi.v18.1.17"`, `OMP_NATIVE_TOOL_IDS` and `PI_0_57_1_RESPONSE_CONSUMER_ID`. This follows the accepted grep-based method in ACR-20260927-pi-0-57-1-historical-clone §2, because no LSP server is configured on this host.

Existing surfaces changed:

- `breadboard_engine/compilation/provider_response.py`:
  - adds the sibling constant `OMP_16_2_13_RESPONSE_CONSUMER_ID = "breadboard.oh-my-pi.v16.2.13"` and a streaming entry in `_NATIVE_RESPONSE_CONSUMER_MODES`;
  - adds a parallel binding check for target `oh-my-pi-r2@16.2.13`. The check requires target version 3, streaming, `max_completion_tokens`, no store, no strict tools, `n` sampling only, include_usage, and max output 2048.
  - The check reads `runtime_profile.request_policy` strictly: `preserve_thinking` must be a boolean and `chat_template_kwargs` an object, and both must equal the compiled profile policy.
  - The 18.1.17 branch is unchanged.
- `breadboard_engine/provider/profiles.py`: `OpenAICompletionsRequestPolicy` gains two optional fields that default to `None`.
  - `preserve_thinking: bool | None`.
  - `chat_template_kwargs: Mapping[str, JSON scalar] | None`, frozen as a `MappingProxyType`.
  - When set, each field is emitted in `chat_request`, listed in `required_request_features`, and recorded with effective provenance.
  - `as_dict` omits both fields while they are unset, so existing profile identities and digests are unchanged.
  - Setting `preserve_thinking` requires `supports_thinking_control`.
  - Source: pinned `@oh-my-pi/pi-ai/src/providers/openai-shared.ts:883-886` (qwen thinking format) and `@oh-my-pi/pi-catalog/src/compat/openai.ts:387-398`.
- `breadboard_engine/provider/runtimes/openai/chat.py`:
  - adds the 16.2.13 consumer to the pass-through messages/tools sets;
  - routes `preserve_thinking` and `chat_template_kwargs` into `extra_body` next to the existing `enable_thinking` handling. Existing profiles never set them.
- `breadboard_engine/compilation/server_compiler.py`: tool parameter names are validated by a new `_PARAMETER_NAME_RE`, which also admits a leading underscore. The pinned 16.2.13 `edit` wire schema declares the member `_input` verbatim. The general `_IDENTIFIER_RE` used for every other compiled identifier is unchanged.
- `breadboard/rl/harness/policy_provider.py`:
  - adds the consumer→target map entry and the native-consumer and streaming-renderer set memberships;
  - adds a separate `_omp_16_2_13_public_config` built from `runtime_profile.model_registry`. It uses an exact field set, has no `.get` defaults, and fails closed.
  - The 18.1.17 branch is untouched.
- `breadboard/rl/harness/native_stream_profiles.py`: adds a `NativeStreamProfile` entry for the 16.2.13 consumer and `_omp_16_2_13_state`.
  - `_omp_16_2_13_state` requires `model_config` (`id`, `provider`, `api`, `cost`), `runtime_inputs`, `current_date_time` and `length_aborted_message` from the worker bootstrap, with no defaults.
  - The profile uses the existing optional `provider_failure_phase` and `parse_arguments_phase` fields (ACR-20260927-pi-0-57-1-historical-clone §3). It adds no new `NativeStreamProfile` fields and no Conductor changes.
- `breadboard/rl/harness/sandbox.py`:
  - adds `OMP_16_2_13_LOCAL_ADAPTER_ID = "oh-my-pi.local.v16.2.13"`, `OMP_16_2_13_NATIVE_TOOL_IDS = ("bash","edit","generate_image","read","write")` (sorted, as installed-adapter composition requires; wire order stays in `tool-surface.json`) and the `NATIVE_PHASE_TOOL_IDS` entry;
  - adds the worker-env branch: `OMP_NATIVE_WORKER_FRAMED=1`, `OMP16213_CODING_AGENT_NODE_MODULES=<runtime_root>/node_modules`, `OMP_OFFLINE=1`;
  - exports both new names in `__all__`.
- `breadboard/product/harness/targets.py`: adds a `_NATIVE_WORKER_RECIPES` entry for `oh-my-pi-r2@16.2.13` and adds the consumer to the `lower_e4_target` native-consumer set.
- `config/e4_targets/index.json`: adds an entry for the new package.
- `pyproject.toml`: package-data globs for `oh_my_pi/16.2.13-r2/**`.
- `tests/test_breadboard_cli_packaging.py` and `tests/test_e4_targets.py`: expected-file and target-id lists.
- `tests/rl/harness/test_sandbox_native_phase_admission.py`: every `NATIVE_PHASE_TOOL_IDS` entry must pass installed-adapter admission, which requires sorted, unique IDs. The OMP 16.2.13 entry must equal the target's wire tools.

New surfaces:

- `breadboard/rl/harness/runners/omp_16_2_13_native_tool_worker.ts`: the framed Bun worker with phases `initialize`, `project_request`, `parse_streaming_json_batch`, `prepare_tools`, `execute_batch`, `project_provider_failure` and `close`.
  - It imports only through `OMP16213_CODING_AGENT_NODE_MODULES`, which the sandbox launch environment sets.
  - The model object is built by the pinned `ModelRegistry`. Its `cost` therefore comes from pinned `finalizeCustomModel` and the bundled catalog reference (`model-registry.ts:606-614`), not from BB configuration.
  - The length-abort tool-result text is read from pinned `agent-loop.ts`, and `current_date_time` is the declared `current_date` runtime input (pinned `system-prompt.ts:666-667`). Rule discovery is left to pinned `createAgentSession`.
  - The provider-failure phase gives the pinned SDK only the upstream status and body; it invents no status text or headers.
- `breadboard/rl/harness/omp_16_2_13_native_tools.py`: host-side argument parsing, bound to the pinned root.
- `breadboard/rl/harness/runners/omp_16_2_13_semantics.py`: `Omp16213SemanticsState`, with phase schema `bb.omp-native.v16.2.13` and trace schema `bb.e4.omp-replay-trace.v1`.
- `config/e4_targets/oh_my_pi/16.2.13-r2/` (`target.json`, `harness.yaml`, `prompts/system-prompt.md`, `native-config.json`, `native-worker.json`, `tool-surface.json`, `LICENSE.txt`, `source.json`): target `oh-my-pi-r2@16.2.13`. All 5 wire tools are kept verbatim from the capture.
- `conformance/comparators/oh_my_pi_16_2_13.py` and `tests/e4_parity/fixtures/omp_16_2_13_supplier_cases/`. The fixtures are copied from the r2 declared cases plus the packet's `omp16213_capture_cases.json`, with a manifest that binds the packet SHA-256, member paths and file SHA-256.
  - The cancel case (o4) is compared through the `bb.rl.runner-event-ledger.v2` input contract established for Pi 0.57.1 (PR #151).
- `tests/fixtures/omp_16_2_13_node/{package.json,bun.lock,patches/@ark%2Fschema@0.56.0.patch}` and `.github/workflows/omp-16-2-13-historical.yml`. The workflow runs `bun install --frozen-lockfile --ignore-scripts`, verifies the installed integrity, then runs the 16.2.13 suites with `BB_REQUIRE_PINNED_OMP16213_NODE=1`.
  - The upstream 16.2.13 CLI bundle (`dist/cli.js`) bakes in arktype 2.2.1 and @ark/schema 0.56.0, carrying the root `patchedDependencies` patch of upstream commit 5356713e (`package.json` and `bun.lock` at that commit; patch git blob `c51c5abd`). That patch keeps tool-schema keys in declaration order. The unbundled `src/` modules the worker imports would otherwise resolve unpatched arktype 2.2.5 / @ark/schema 0.56.4, which alphabetize required keys. The fixture closure therefore reproduces the upstream build: `overrides` pin those four packages to the upstream lock versions (integrities equal to the upstream lock), and `patchedDependencies` applies the verbatim upstream patch.
  - The worker and `omp_16_2_13_native_tools.PINNED_MODULE_SHA256` pin the patched `@ark/schema/out/constraint.js`, so an unpatched root fails closed.
- Tests: `tests/rl/harness/test_omp_16_2_13_*.py` and `tests/e4_parity/test_omp_16_2_13_comparator.py`.

Outside scope:
- These stay byte-identical: `config/e4_targets/oh_my_pi/16.2.13/**` and its index entry, and `config/e4_targets/pi/0.57.1/**` (index digest `0131e41b0ffbbd0800fd332b769de2603caf4716382989a50c9e47a8f8d2441c`).
- The 18.1.17, Pi, OpenClaw and Hermes behavior is unchanged.

## 3) Coupling and Generalization Impact

The 16.2.13 consumer is a sibling of the 18.1.17 consumer, with its own identity constants, profile entry, worker, semantics module and target package. There are no aliases, no version flags inside `omp_semantics.py`, and no profile names or `target_id ==` branches in the Conductor.

The Conductor is not modified. Upstream threshold auto-compaction (o3) emits only `auto_compaction_start`/`auto_compaction_end` events in print mode and has no model-visible effect. It is reported as the named divergence `compaction_start_then_exit` rather than modeled through a new loop hook.

The shared-code changes are three:
- the two optional request-policy fields, which default to `None` and are omitted from identities;
- the extra_body routing in `chat.py`, which is inert unless the fields are set;
- the parameter-name regex, which is strictly wider and applies only to tool parameter names.

Coupling risk is low to medium.

## 4) Change Classification

- Classification: `additive`

## 5) Evidence and Validation Plan

- Parity oracle: r2 packet SHA-256 `cf93d5ac54c0271ce90a7028b0c1cde6ff022f7516cb26a838608878e5f54ec9`. The fixture builder `verify` step re-checks every copied member.
- Conductor tests run the real Conductor, the real pinned Bun worker, and an SSE provider that replays the captured chunks, for o0-o12, o6b and f2.
  - Comparator verdicts come only from real BB traces.
  - o4 (external SIGINT at the anchor) publishes no trace, so at Conductor level it is asserted on its prefix facts only. Its comparator verdict comes from the installed replay through the ledger input.
- Normalizations are typed and counted in every report: `workspace_root`, `home_root`, `package_dir_documentation_path`, `current_date_reminder`, `workstation_os_release`, `message_timestamp`, `bash_wall_time` and `request_limit_cause`.
- Named divergences are reported and never normalized: `sdk_transport_headers`, `advertised_image_tool_unexercised`, `json_member_order`, `bounded_request_cap`, `compaction_start_then_exit`, `upstream_cli_no_rules`, `external_cancel_signal`, `workspace_regular_files_only`, `sdk_hidden_transport_retry`, `truncated_stream_rejected`, `unmodeled_finish_reason` and `non_http_transport_failure`.
  - `upstream_cli_no_rules` applies only when the upstream argv carries `--no-rules` (o6b; pinned `main.ts:927-929`). The target keeps rule discovery, and the comparator scopes a system prompt only when removing exactly one `<generic-rules>` block makes it equal upstream. Any other difference remains a finding.
- CI: `.github/workflows/omp-16-2-13-historical.yml` must pass. The danger-zone ACR guard must pass on the changed-file list.
- The installed DO-2 replay (wheel built from the exact head, run inside a SIF) is a separate evidence step. This ACR claims no installed replay and no external acceptance.

## 6) Rollback Plan

If a 16.2.13 comparison diverges outside the named divergences, or an existing profile's behavior changes, revert through the protected review path. The pinned v1 `oh-my-pi@16.2.13` package and the 18.1.17 lane are untouched, so reverting removes only sibling surfaces, the two optional request-policy fields and the parameter-name regex. Rerun the OMP, Pi and provider suites and the danger-zone ACR guard on the rollback candidate.

## 7) Approvals

This ACR records the design in H/omp16213-clone-design.md and the grep-based site enumeration. Independent review of the exact head, the applicable CI and the protected merge decision are pending. Main and the campaign owner keep those decisions. This document grants no merge authority and no DO-2 qualification.
