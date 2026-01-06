# Handoff V12 — KyleCode IR & Capability Architecture (Days 1–15 Roadmap)

> Comprehensive handoff for the incoming engineer continuing Phase 2. It assumes no internal context beyond access to this repository and general programming expertise.

## 1. Project Overview

KyleCode is a configurable research platform for agentic coding. Its charter is to reproduce, evaluate, and A/B test behaviors seen in modern IDE assistants (OpenCode, Claude Code, Cursor, etc.) while keeping all major choices (models, tool catalogs, dialects, prompts) declarative and versionable. Core goals:

- Drive repeatable filesystem build/test tasks (“C filesystem” benchmark) end-to-end.
- Allow providers to be swapped without touching executor logic (adapter pattern + IR).
- Collect rich logs/telemetry for offline analysis and benchmarking.
- Support both text-based and provider-native tool calling.

Phase 2 deepens the architecture: structured IR, capability catalogs, TPSL-driven prompts, Ray isolation, and enhanced logging. The repository already contains multiple agent configs, TPSL templates, and logging v2 infrastructure capturing transcripts, prompts, API payloads, and tool artifacts.

## 2. Repo Landmarks (updated for V12)

- `agentic_coder_prototype/`
  - `agent_llm_openai.py` — Agent loop orchestrator; now emits IR delta events and conversation snapshots.
  - `provider_runtime.py` — Provider runtimes (OpenAI Chat/Responses, Anthropic, OpenRouter, mock runtime) with retry/backoff logic.
  - `provider_routing.py` — Provider registry + capability matrix exposure.
  - `provider_ir.py` — New IR dataclasses + conversion helpers.
  - `execution/` — Enhanced executor with Makefile preflight guard and path normalization.
  - `compilation/` — V2 loader (nested extends fix), TPSL compiler.
  - `state/session_state.py` — Session state now tracks IR events/finish metadata and writes conversation IR snapshots.
- `docs/`
  - `phase_2/HANDOFF_V11.md` — Previous handoff; kept for reference.
  - `phase_2/HANDOFF_V12.md` — This document.
- `agent_configs/`
  - V2 configs for OpenAI/OpenRouter/DeepSeek + mock provider; now default to `system_compiled_and_persistent_per_turn` for stricter plan/build phases.
- `implementations/tool_prompt_synthesis/` — TPSL templates updated to enforce plan discipline and immediate skeleton creation.
- `logging/` — Run directories containing conversation transcripts, raw API payloads, tool results, and (new) `meta/conversation_ir.json`.
- `tests/`
  - `test_provider_ir.py` — New IR-focused unit tests.
  - Existing executor, prompt, and integration tests (Ray-based suite still blocked by socket restrictions in this environment).

## 3. Phase 2 Deliverables to Date (V12)

### 3.1 Structured IR & Logging
- Introduced explicit, versioned provider IR (`IRConversation`, `IRMessage`, `IRDeltaEvent`, `IRFinish`, etc.).
- `SessionState` collects IR delta events (text/tool_call/finish) and finish metadata; snapshots now include `conversation_ir.json` alongside the legacy transcript.
- Added unit tests covering IR conversion, conversation IR assembly, and snapshot persistence.
- `run_agentic_loop` persists the IR per run (even in exception scenarios) and stores normalized usage + provider metadata for future migrations.

### 3.2 Capability Matrix & Metadata
- Created a capability descriptor (`tool_calls`, `streaming`, `json_mode`, `reasoning`, `caching`) per provider (`provider_capabilities.py`).
- Exposed `ProviderRouter.get_capabilities(...)`; agent metadata now includes capability info for planners and offline analysis.

### 3.3 Streaming Normalization & Usage Telemetry
- Streamed provider responses generate IR delta events; tool call payloads are captured per turn.
- Normalized token usage (`prompt_tokens`, `completion_tokens`, cache hits) is stored consistently across providers.
- Provider runtimes aggregate provider metadata + retry history for logging.

### 3.4 Retry & Diagnostics for HTML Payloads
- `OpenAIBaseRuntime._call_with_raw_response` retries HTML payloads (two attempts with 0.4s/0.9s backoff) and raises `ProviderRuntimeError` detailing `html_detected`, snippet, status, attempts, and retry schedule.
- This surfaces OpenRouter/other provider throttling clearly without crashing the loop.

### 3.5 Makefile Preflight & Template Guidance
- Enhanced executor blocks `make*` commands until a Makefile exists with `all/test/clean` targets; runs return a validation failure with a hint if prerequisites are missing.
- TPSL plan/build templates emphasize early skeleton creation, workspace-relative paths, and quick progression from plan to build.
- Builder prompt discourages `set -o pipefail` and encourages `set -e` / `bash -lc` for predictable failure handling.

### 3.6 Mock Provider Enhancements
- Added a mock runtime (no network) that produces consistent tool calls and git-compatible patches; used for offline smoke tests.
- Ensures diffs apply cleanly and produce tool result artifacts (workspaces show generated Makefile, header, source, stub test).

### 3.7 Safety Hardening
- `run_agentic_loop` now guards against exceptions inside `_run_main_loop`, capturing the stack trace, writing `errors/run_loop_exception.json`, and returning a structured `completion_summary` with reason `run_loop_exception` to prevent `None` results.
- V2 config loader resolves nested `extends` recursively, ensuring derived configs inherit `version`, `tools`, `modes`, `loop`, etc. (fixes missing `tools.registry.paths` in derived configs).

### 3.8 Tests & Smoke Runs
- IR unit tests (`tests/test_provider_ir.py`) and existing prompt tests pass.
- Ray-based tests still fail in this environment (socket permissions); noted for future engineer.
- Mock-run smoke tests produce full IR + tool artifacts; real provider runs still hinge on API stability (OpenRouter HTML intermittently blocks progress with clear diagnostics).

## 4. Problems Solved So Far

- **Configuration Inheritance:** V2 loader now supports nested `extends`, allowing derived configs to inherit schema correctly without manual duplication.
- **Tool Prompt Mode Discipline:** Switched configs to `system_compiled_and_persistent_per_turn`, eliminating plan contamination from legacy per_turn_append prompts.
- **Plan Mode Enforcement:** TPSL templates enforce workspace-relative paths, limit exploration to two turns, and push early skeleton creation.
- **Makefile Prerequisite Guard:** Prevents agents from running `make*` before Makefile + targets exist, reducing erroneous command failures.
- **HTML Payload Retry:** Short, logged backoff makes provider HTML errors actionable (instead of silent parse failures).
- **IR Persisted Per Run:** Complete conversation IR (messages + events + finish) stored for downstream analytics; snapshots include `ir_version` for migrations.
- **Capability Metadata:** Run metadata now captures provider capabilities, enabling planners/adapters to respond to provider quirks systematically.
- **Run-Loop Exception Handling:** Agent loop never returns `None`; exceptions logged and summarized; run artifacts remain consistent.

## 5. Current Direction (Days 1–7 completed)

The first 7 days focused on establishing the structured IR and capability matrix (spec’d in `docs/LLM_PROVIDER_V2_IR_RECOMMENDATIONS.md`). As of V12:

- IR dataclasses exist; logs store conversation IR; pyro tests verify conversions.
- Provider capabilities are defined and attached to run metadata.
- Streaming responses produce IR delta events; normalized usage is recorded; retry/backoff instrumentation added for HTML errors.
- Mock runtime validates diff/application and persists tool result artifacts.
- Makefile guard + plan/build templates reduce early-phase stalling.

## 6. 15-Day Outline (High-Level Plan)

### Days 1–7 (COMPLETE)
1. **Define IR and integrate persistence** (✔)
2. **Expose capability matrix and provider metadata** (✔)
3. **Normalize streaming events, usage telemetry, and retry diagnostics** (✔)

### Days 8–11 (Next)
4. **Enhance run lifecycle instrumentation**
   - Instrument `_run_main_loop` to record termination cause (`loop_exit`, `max_steps_exhausted`, `provider_error`), even in local mode.
   - Emit unified finish events containing normalized usage, finish reason, and provider metadata.
   - Ensure `completion_summary` always includes `steps_taken`, `max_steps`, and simple “reason” tags for analytics.
5. **Improve provider error resilience**
   - Add a typed retry/backoff outcome (e.g., `retry_exhausted_html`) and optional retained snippet for offline inspection.
   - Consider caching IR for partial runs in addition to final snapshots.

### Days 12–15 (Future Sprint Scope)
6. **Extend IR to replay/integration tests**
   - Add record/replay fixtures to validate IR across provider SDK updates.
   - Ensure the event bus is the single source of truth for streaming transcripts.
7. **Parallel tool group semantics**
   - Introduce `ToolCall.group` semantics in planners; adapt adapters for providers lacking real parallelism.
   - Relay tool results accordingly (batch results for parallel providers, sequential for others).

## 7. Current Obstacles / Known Issues

- **Provider HTML payloads** — External issue. With retries + diagnostics, runs fail gracefully but still block progress. A follow-up might add `html_detected` metrics and optional fallback to cached responses.
- **Local-mode runs end with `completion_reason="no_result"`** — Fallback prevents crashes; a dedicated follow-up should instrument exit conditions to capture final state (Days 8–9 item).
- **Ray tests blocked** — Ray cluster init fails (socket permissions). When running outside restricted environment, re-run `pytest tests/test_agent_session.py -q`.

## 8. Next Steps (Immediate)

1. **Instrument `_run_main_loop` termination**
   - Record final state (`completed`, `reason`, `steps_taken`, `loop_exit` vs `provider_error`). Even if a run returns `no_result`, future engineers should see why.
   - Write these details to `conversation_ir.finish` and `completion_summary` for analytics.

2. **Create a follow-up story for provider HTML resilience**
   - Possibly whitelist specific HTML patterns (rate limit, throttle) and surface clearer hints (e.g., “OpenRouter rate-limited your request”).

3. **Ray test re-enable**
   - Use `scripts/run_ray_session_tests.sh` (outside sandbox) to execute `pytest tests/test_agent_session.py -q` once socket restrictions are lifted and verify executor flows.

## 9. Verification / Running the System

### Environment
- `.env` — Set `OPENAI_API_KEY` or `OPENROUTER_API_KEY`; for mock runs no API key required (mock uses sentinel).
- `conda run -n ray_sce_test ...` environment recommended (or equivalent).

### Smoke Tests
- **Mock (offline):**
  ```bash
  AGENT_SCHEMA_V2_ENABLED=1 RAY_SCE_LOCAL_MODE=1 \
  python -u main.py agent_configs/opencode_mock_c_fs.yaml --schema-v2 \
         --task implementations/test_tasks/universal_agentic_debugging_task.md --max-iterations 3
  ```
  Check `logging/<timestamp>/meta/conversation_ir.json` + `provider_native/tool_results/` for makefile skeleton.

- **Providers (network):**
  ```bash
  python -u main.py agent_configs/opencode_openrouter_glm46_c_fs_perturn_append.yaml --schema-v2 \
         --task implementations/test_tasks/universal_agentic_debugging_task.md --max-iterations 5
  ```
  If `ProviderRuntimeError` (HTML), review the `details` in console/logging for snippet + retry schedule.

- **IR Snapshot:**
  Run `python scripts/log_reduce.py logging/<dir> --tool-only --turn-limit 5` and inspect `meta/conversation_ir.json` for IR integrity.

### Unit Tests
- `pytest tests/test_provider_ir.py -q`
- `pytest tests/test_prompt_compiler_v2.py -q`
  *(Ray tests currently blocked by sandbox)*

## 10. File-by-File Summary of Notable Changes

- `agentic_coder_prototype/provider_ir.py` — New IR dataclasses + conversion helpers.
- `agentic_coder_prototype/state/session_state.py` — Tracks IR events/finish; snapshots include conversation IR.
- `agentic_coder_prototype/agent_llm_openai.py` — Emits IR delta events, captures usage, handles run-loop exceptions, writes IR snapshot per run.
- `agentic_coder_prototype/provider_routing.py` — Added capability matrix lookups; mock provider entry.
- `agentic_coder_prototype/provider_runtime.py` — Retry/backoff diagnostics, mock runtime patch upgrade.
- `agentic_coder_prototype/execution/enhanced_executor.py` — Makefile preflight guard, workspace root normalization for V2 configs.
- `implementations/tool_prompt_synthesis/*` — Stricter plan/build templates; early skeleton guidance.
- `docs/phase_2/HANDOFF_V12.md` — This handoff.
- `tests/test_provider_ir.py` — IR conversion and snapshot tests.

## 11. Deliverables at a Glance

- Structured IR stored per run, enabling consistent downstream analytics.
- Capability matrix per provider; planning/execution aware of provider semantics.
- Enhanced logging of streaming events, normalized usage, and HTML retry diagnostics.
- Offline mock runtime that exercises diff + tool result flows post-IR change.
- Makefile preflight guard, plan/build templates encouraging early skeleton creation.

## 12. Outlook

Days 1–7 objectives are complete: IR defined, persisted, events emitted, capabilities exposed, retry/backoff improved. The next sprint should focus on lifecycle instrumentation (explicit termination reasons), enrichment UI/tools to leverage the IR, and expanding the IR to record/replay fixtures while ensuring minimal scope creep.

Once run termination instrumentation is in place (Days 8–9), the foundation will be ready to onboard additional providers or support UI replay based on the IR.

## 13. Appendix: Quick Reference Commands

- **IR Snapshot Check**
  ```bash
  latest=$(ls -1 logging | sort | tail -n 1)
  cat logging/$latest/meta/conversation_ir.json
  ```
- **Provider Capabilities**
  ```python
  from agentic_coder_prototype.provider_routing import provider_router
  print(provider_router.get_capabilities("openrouter/openai/gpt-5-nano"))
  ```
- **Mock Run** (no network)
  ```bash
  AGENT_SCHEMA_V2_ENABLED=1 RAY_SCE_LOCAL_MODE=1 python -u main.py agent_configs/opencode_mock_c_fs.yaml --schema-v2 \
      --task implementations/test_tasks/universal_agentic_debugging_task.md --max-iterations 3
  ```

---

Use this document as the central handoff reference. For any engineer picking up Phase 2, validate the IR snapshots, inspect the capability metadata, and proceed with the outlined “Next steps” to harden run lifecycle handling and handle provider-specific anomalies. Once those are in place, we can graduate to the Day 12–15 goals (record/replay fixtures, native parallel semantics) without architectural rework.
