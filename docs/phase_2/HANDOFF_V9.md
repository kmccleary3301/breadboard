# Handoff V9 — KyleCode Agentic Coding Platform

## 1. Project Overview
- **Mission**: KyleCode is a modular agentic coding backend designed to reproduce and experiment with commercial IDE agents (OpenCode, Claude Code, Cursor, Codex, etc.) while remaining composable enough for RL and AutoOptim research. Everything — providers, tool schemas, sandboxes, prompt catalogs, loop strategies — is driven by Schema V2 YAML so that a single config can clone a target harness end to end.
- **Runtime Stack**: Ray (actor orchestration), gVisor/Docker (sandbox isolation), customizable workspace mirrors, LSP orchestration, provider adapters (OpenAI, OpenRouter, Anthropic), and a Tool Prompt Synthesis Layer (TPSL) built on Jinja templates. Logging V2 snapshots every run with transcripts, prompts, native provider payloads, and a copy of the final workspace.

## 2. Current Architecture Snapshot
- **Compilation layer** (`agentic_coder_prototype/compilation/*`): Resolves Schema V2 inheritance, compiles prompts, loads YAML tool registries, applies overlays, and caches results.
- **Execution layer** (`agentic_coder_prototype/execution/*`): EnhancedToolExecutor handles validation, ordering, LSP feedback, concurrency guards, and now performs dialect-specific payload normalization before delegating to the sandbox.
- **Sandbox layer** (`kylecode/sandbox_v2.py`): Process/Docker-backed workspaces that expose file, shell, git, and LSP operations. Git apply is still the single entrypoint for patching once the payload is normalized.
- **Dialects layer** (`agentic_coder_prototype/dialects/*`): Textual tool-call parsers. Pythonic XML, Bash block, unified diff, OpenCode patch, etc., are selected per config/mode.
- **Logging layer** (`logging/<timestamp>_*`): Captures mode/config metadata, tool transcripts, provider-native IO, and a copy of the workspace (pending cleanup automation from V8 TODOs).

## 3. What Landed Since Handoff V8
- **OpenCode Patch Standardization**
  - `agentic_coder_prototype/dialects/opencode_patch.py` now emits a single `patch` tool call carrying the original `*** Begin Patch … *** End Patch` block instead of building `create_file_from_block` stubs. This matches OpenCode semantics and simplifies prompt instructions.
- **Unified Diff Adapter**
  - New module `kylecode/opencode_patch.py` parses OpenCode patch blocks into structured operations and converts them into unified diffs (`operations_to_unified_diff`, `to_unified_diff`).
  - EnhancedToolExecutor converts incoming `patch` calls to `apply_unified_patch` calls after running the adapter, so execution still funnels through `git apply`. Failures bubble up as validation errors with human-readable messages.
- **Tool/Config Cleanup**
  - `agent_configs/opencode_grok4fast_c_fs_v2.yaml` now prefers only `opencode_patch` (plus bash) and no longer advertises unified diffs or yaml_command fallbacks for Grok.
  - `implementations/tools/defs_oc/opencode_patch.yaml` copy describes the single canonical format.
  - Dry-run harnesses (`tests/test_dry_run*.py`) reference the simplified tool set so they exercise the new payload flow.

## 4. Problems Addressed
- **Model Confusion Between Diff Dialects**: Agents previously oscillated between unified diff, Aider search/replace, and OpenCode add-file blocks, leading to failed tool calls. The config and dialect changes restrict Grok to one diff syntax.
- **Lack of Execution Path for OpenCode Blocks**: Without an adapter, `*** Begin Patch` payloads couldn’t reach the sandbox. The new conversion layer keeps the sandbox unchanged while preserving OpenCode instructions for the model.
- **Test Drift**: Dry-run fixtures now reflect the actual tool inventory we expect to expose, preventing regressions from silently leaning on deprecated helpers.

## 5. Known Limitations & Open Questions
1. **schema/base defaults**: `agent_configs/base_v2.yaml` still lists legacy diff helpers (`apply_unified_patch`, `apply_search_replace`) in aliases and preferences. Decide whether to globalize the single-patch approach or keep the legacy formats for non-OpenCode profiles.
2. **Adapter Coverage**:
   - Only handles `Add/Update/Delete` directives. OpenCode also supports `*** Move File:` / `Rename` patterns — not yet implemented.
   - Assumes text files; binary/large files will be read fully by `_fetch_file_content_for_patch`. Consider safeguards or streaming.
   - No caching for repeated file reads inside the same patch (e.g., multiple updates to the same file in one block).
3. **Prompt Collisions**: Tool prompt synthesis still references unified diff templates for some modes; ensure configs that reuse the OpenCode profile don’t inject conflicting guidance.
4. **Testing**: No dedicated unit tests yet for `kylecode/opencode_patch`. DRY_RUN exercises end-to-end flow but doesn’t assert conversion correctness or error handling.
5. **Logging TODOs from V8**: Workspace cleanup and final workspace copying are still outstanding. After the patch pipeline settles, revisit those tasks to prevent mirror recursion.
6. **Telemetry**: New conversion code paths aren’t emitting metrics. If AutoOptim tracking needs per-format success metrics, instrument after integration tests pass.

## 6. Recommended Next Steps
1. **Expand Patch Adapter**
   - Add rename/move handling and better diagnostics (line numbers, offending directive) for parse failures.
   - Introduce unit tests covering add/update/delete, malformed blocks, duplicate sections, and multi-file patches.
2. **Config Harmonization**
   - Decide whether to prune legacy diff tools from base profiles or to keep them for other research harnesses. Update documentation and tests accordingly.
   - Regenerate tool prompt synthesis cache for affected configs after prompt template updates.
3. **Performance & Robustness**
   - Memoize file reads per patch in `_fetch_file_content_for_patch` to avoid repeated IO.
   - Consider a guard for extremely large diffs (token budgets) before passing them to the model.
4. **Documentation & Telemetry**
   - Update TPSL docs and provider prompt notes to mention the single `patch` tool flow.
   - Hook conversion successes/failures into the enhanced telemetry database for reward tracking.
5. **Workspace Lifecycle (Carry-over from V8)**
   - Implement workspace reset + final snapshot copying, then re-run the Grok filesystem task to confirm no recursive mirrors occur with the new diff pipeline.
6. **Ray-Mode Regression**
   - Once network sockets are permitted, run the same scenario under true Ray actors to confirm the new conversion works in distributed mode and LSP touches still function.

## 7. Validation Status
- No automated suite executed in this iteration (per request). Dry-run harnesses were updated but not executed. Plan to run:
  ```bash
  pytest tests/test_dry_run.py tests/test_dry_run_bad_code.py -q
  ```
  and the Grok filesystem scenario:
  ```bash
  conda run -n ray_sce_test python main.py \
    agent_configs/opencode_grok4fast_c_fs_v2.yaml --schema-v2 \
    --task implementations/test_tasks/universal_agentic_debugging_task.md \
    --max-iterations 20
  ```

## 8. Quick Reference
- **Key files touched**:
  - `kylecode/opencode_patch.py`: OpenCode → unified diff adapter (new).
  - `agentic_coder_prototype/dialects/opencode_patch.py`: Parses patch blocks into tool calls.
  - `agentic_coder_prototype/execution/enhanced_executor.py`: Converts `patch` tool calls prior to sandbox execution and exposes helper hooks.
  - `agent_configs/opencode_grok4fast_c_fs_v2.yaml`: Restricts diff dialects for Grok profile.
  - `tests/test_dry_run*.py`: Updated fixtures matching the streamlined tool inventory.
- **Inspection targets**:
  - `logging/<latest>_agent_ws_opencode/` — verify resulting tool traces still show the single `patch` tool.
  - `implementations/tool_prompt_synthesis/*` cache once prompt templates are regenerated.

## 9. Pending Questions for the Next Owner
- Do we need backwards compatibility with legacy diff formats for other model/provider experiments? If so, determine whether to maintain dual adapters or split profiles.
- How should binary or large-file edits be handled? Currently the adapter expects text and may choke on non-UTF-8 data.
- Should we surface parse/translation diagnostics back to the model (e.g., via tool error messages) to encourage self-correction?
- Is there appetite for shared patch utilities (OpenCode ↔ unified diff ↔ Aider) to reduce duplication if other harnesses need conversions?

## 10. Closing Notes
The platform is now close to a true OpenCode mirror for the Grok profile: models see one diff syntax, the executor enforces a single execution path, and configs encode the intended behavior. Focus next on hardening the adapter, lining up documentation/tests, and finishing the workspace lifecycle improvements queued since V8.
