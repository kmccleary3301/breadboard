# Tool Definitions (YAML)

This directory holds individual YAML tool definitions under `defs/`. Each file defines a single tool with:

- id, name, description, type_id
- parameters (name, type, required, default, description)
- manipulations (capabilities: e.g., shell.exec, file.read, file.write, diff.apply)
- syntax_formats_supported, preferred_formats
- execution (blocking, max_per_turn)
- provider_routing (native_primary and fallback formats per provider)

Loader: `tool_calling/tool_yaml_loader.py` loads and validates these YAML files into `EnhancedToolDefinition`s and a `manipulations_by_id` map that the executor can use to route logic without switch statements.

Examples:
- `run_shell.yaml`: shell command execution with one-bash-per-turn policy
- `read_file.yaml`: selective file reads
- `list_dir.yaml`: directory listing with optional depth
- `create_file.yaml`: create empty files
- `apply_unified_patch.yaml`: unified-diff patch application
- `apply_search_replace.yaml`: search/replace edits (Aider style)
- `create_file_from_block.yaml`: add-file helper for diff-authoring workflows
- `mark_task_complete.yaml`: finalize session

`defs_codex_139/` contains the isolated Codex 0.139.0 gpt-5.5 Responses catalog,
checked against `recordings-gpt55/`. Its `responses_tool_schema` preserves custom
patch grammar, client tool search, deferred namespaces, and hosted web search
without Chat-schema conversion. Historical `recordings/` used an unknown-model
fallback and are not normative for this profile.
Only profiles selecting that directory and `tools.native_semantics: codex_0_139`
use the corresponding stock execution behavior.
The profile registers the multi-agent namespace for execution but selects
`responses_deferred_tools: [multi_agent_v1]`, so the eleven upfront tools match
stock. Client search returns the discovered namespace in `tool_search_output`,
not in the next request's tools array. `responses_custom_tools: [apply_patch]`
and `responses_tool_search: true` preserve their native call/output types.
`responses_preserve_tool_call_items: true` retains raw function argument strings
and namespaces (`protocol/src/models.rs:793-806`); otherwise ordinary profiles
keep their existing canonical replay. `responses_tool_output_items: true` enables
the closed canonical image/text output carrier and emits stock content-item
arrays, never stringified image JSON (`protocol/src/models.rs:1310-1324,1391-1395`).
`responses_hosted_outputs: [web_search_call]` preserves hosted search calls
through canonical terminal records and continuation requests, using stock's
closed action variants (`protocol/src/models.rs:869-879,1193-1220`).

Build the native image codec explicitly before using `view_image`:
`python -m breadboard_engine.execution.codex_native build image`. This runs a locked release build with
stock-pinned Rust image dependencies. Missing helpers return that build command
as a visible error; tools do not compile helpers or substitute Python codecs.

Before using `exec_command` or custom `apply_patch`, also run `python -m breadboard_engine.execution.codex_native build patch`.
The locked helper links the revision-pinned stock apply-patch parser and executor,
including heredoc and `cd` interception; it never relies on an `apply_patch` found
on PATH. Command sessions, image decoding (including metadata checks), and the
patch helper run under BreadBoard's restricted process policy, including protected
credential paths.

Set the selected `providers.models[]` entry's `tool_output_truncation` to the
stock model's policy: the dated gpt-5.5 profile uses `{mode: tokens, limit: 10000}`.
Exec and continuation output use that model-selected limit, not a catalog-wide
default. Historical unknown-model metadata supplies `{mode: bytes, limit: 10000}`
in `models-manager/src/model_info.rs:91`; `protocol/src/protocol.rs:2965-2972`
converts bytes to tokens only where a token cap is required. Patch output retains
the original byte/token policy for its inner formatter.

The selected model's `supports_image_detail_original` capability enables
`view_image(detail: original)` without resizing. Its default is false, matching
`protocol/src/openai_models.rs:380-381`; `tools/src/image_detail.rs:6-8` selects it.

Each Codex thread owns its process manager and 64-session limit; continuation
IDs cannot access another thread's sessions. Post-exit output draining is bounded,
even when a detached descendant keeps stdout open. Production IDs are uniform
integers in `[1000, 100000)` with collision redraws. Request-capture lanes may
install a strict source before the first allocation through
`codex_runtime(owner).set_process_id_generator(source, thread_id=None)` and inspect
`codex_runtime(owner).process_id_generator_state()`; exhaustion is an error, not wraparound.
Every `exec_command`, including patch interception and completed commands,
allocates an ID; `write_stdin` does not.

Source citations below are relative to `codex-rs/` at
`a7dff904308535e965aee87680c1fc5ef1d19eec`:
- `exec_command` / `write_stdin` schemas: `core/src/tools/handlers/shell_spec.rs:21-151`.
- Plan schema/output: `core/src/tools/handlers/plan_spec.rs:7`,
  `core/src/tools/handlers/plan.rs:22-40`.
- User input schema/rejection: `core/src/tools/handlers/request_user_input_spec.rs:10-95`.
- Image schema/content item: `core/src/tools/handlers/view_image_spec.rs:15`,
  `core/src/tools/handlers/view_image.rs:120-191,223-237`.
- Multi-agent namespace and members: `core/src/tools/handlers/multi_agents_spec.rs:11-12,49-79,120-156,219-254,290`.
- Goal schemas: `ext/goal/src/spec.rs:13-60`.
- Hosted search: `core/src/tools/hosted_spec.rs:20-49`.
- Exec response text: `core/src/tools/context.rs:399-434`.
- Exec defaults/random IDs: `core/src/unified_exec/mod.rs:64-72,165-178`,
  `core/src/unified_exec/process_manager.rs:332-366`,
  `core/src/session/session.rs:992-994`.
- Token count/truncation: `utils/string/src/truncate.rs:38-152`,
  `utils/output-truncation/src/lib.rs:12-25`.
- Polling, write restrictions, and draining: `core/src/unified_exec/process_manager.rs:595-738,1082-1168`.
- Image byte preservation/resizing/encoding: `utils/image/src/lib.rs:55-195`.
- Patch interception and response: `core/src/tools/handlers/unified_exec/exec_command.rs:229-254`,
  `core/src/tools/handlers/apply_patch.rs:509-611`,
  `core/src/tools/runtimes/apply_patch.rs:226-255`, `core/src/tools/mod.rs:62-86`,
  `core/src/tools/events.rs:344-349` (original byte/token inner policy).
- Spawn errors: `core/src/tools/handlers/unified_exec/exec_command.rs:303-305`,
  `core/src/tools/runtimes/unified_exec.rs:402`,
  `core/src/unified_exec/process_manager.rs:987,1078`.
- Hosted-search replay and catch-all action: `protocol/src/models.rs:869-878,1193-1220`.
- Custom patch declaration/input/output: `core/src/tools/handlers/apply_patch_spec.rs:5-26`,
  `core/src/tools/handlers/apply_patch.rs:332-435`, `protocol/src/models.rs:830-852`.
- Client search declaration and call/output: `core/src/tools/handlers/tool_search_spec.rs:7-61`,
  `core/src/tools/handlers/tool_search.rs:85-106`, `protocol/src/models.rs:807-818,854-859`.
