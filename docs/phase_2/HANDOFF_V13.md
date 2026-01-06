# Handoff V13 — KyleCode IR, Provider Stability, and Phase 2 Continuation

> **Agentic coder quick map.** Start here to avoid aimless file scraping. Critical paths are annotated with why they matter right now.
>
> | Area | Path | Why you should look | Key sections |
> | --- | --- | --- | --- |
> | 🔁 Agent loop & lifecycle | `agentic_coder_prototype/agent_llm_openai.py` | Tracks turn sequencing, plan/build transitions, IR persistence, and where provider retriable errors are handled; new logic for plan turn limiting is injected here. | `_setup_tool_prompts`, `_run_main_loop`, `_invoke_runtime_with_streaming`, plan limit block around lines ~1190–1230, finish metadata block ~820+, provider error handling ~1470+. |
> | 🌐 Provider runtime | `agentic_coder_prototype/provider_runtime.py` | Normalizes responses across providers. The new HTML/body capture lives here, including `_decode_body_text` and `_call_with_raw_response`. | `_call_with_raw_response`, `_decode_body_text`, OpenAI runtime subclasses (chat/responses), SSE handling TODO. |
> | 🧭 Configs in play | `agent_configs/opencode_openrouter_glm46_c_fs_perturn_append.yaml` / `..._grok4fast_c_fs_v2.yaml` / `..._deepseek_v32exp_c_fs_perturn_append.yaml` | These are the OpenRouter routes failing. Note the plan turn limit & tool gating adjustments. | Each file’s `prompts`, `loop.plan_turn_limit`, `turn_strategy`, `provider` blocks. |
> | 🗂️ Logging artifacts | `logging/<timestamp>_agent_ws_opencode/` | Latest failures and captured raw excerpts; reference when re-running tests. | `errors/turn_6.json`, `meta/conversation_ir.json`, `conversation/conversation.md`. |
> | 🛠️ Implementation roadmap | `docs/PROVIDER_FIXES_V2_PLAN.md` | Fresh P0/P1/P2 plan derived from GPT-5 analysis. | Entire document. |
> | 📑 GPT-5 analysis | `docs/PROVIDER_ISSUES_PLANNER_RESPONSE.md` | 20+ page report, distilled below but read directly for nuance. | Sections 1–7 for failure causes and recommended mitigations. |
> | 📚 Prior phase summary | `docs/phase_2/HANDOFF_V12.md` | Previous handoff with IR/logging/capability milestones. Useful for historical context. | Sections 3–8, 12. |

---

## 1. Project recap

**KyleCode** is a configurable platform for running agentic coding experiments end-to-end (planning, tool execution, code synthesis). It emphasises declarative configuration (YAML), provider abstraction (swappable LLM backends), and rich logging/IR for post-run analytics. Phase 2 focuses on structured intermediate representation (IR), capability catalogs, run lifecycle instrumentation, and isolated execution via Ray/local sandboxes.

### Core goals (unchanged from Phase 2 charter)
- Reproduce filesystem build/test tasks consistently (“C filesystem” benchmark).
- Swap providers/adapters without modifying the executor logic.
- Record rich telemetry (IR events, tool output, prompts) for offline analysis.
- Support both text-based and provider-native tool calling.

### Phase 2 progress to date (through V12)
- **IR v1**: conversation snapshots, streaming delta events, finish metadata persisted per run.
- **Provider capability matrix**: unified view of tooling/streaming support per backend.
- **Retry/diagnostics**: detection of HTML payloads, backoff instrumentation.
- **Mock runtimes & Makefile guard**: offline validation + guardrails for `make` usage.
- **Logging V2**: conversation transcripts, raw request/response dumps, tool artifacts.
- **Plan discipline**: stricter templates enforcing early skeleton creation.

Refer to `docs/phase_2/HANDOFF_V12.md` for fine-grained history; V13 builds directly on that baseline.

---

## 2. What’s new since Handoff V12

1. **New incidents with OpenRouter-backed models**
   - GLM-4.6, Grok-4-fast, DeepSeek v3.2-exp runs return non-JSON payloads (sometimes pure whitespace) or stall after the first streamed chunk. Failures persist despite key rotation and plan-mode tuning.
   - Logs captured in `logging/20251010-*` directories; see `errors/turn_6.json` for raw excerpts.

2. **Runtime instrumentation updates**
   - `_call_with_raw_response` now decodes full response bodies (respecting `Content-Encoding`), stores `raw_excerpt`, and preserves HTML payloads for vendor escalation.
   - Plan mode limiter inserted in `_setup_tool_prompts` to auto-disable plan after configurable number of turns (`loop.plan_turn_limit`).

3. **Implementation plan drafted (`docs/PROVIDER_FIXES_V2_PLAN.md`)**
   - Structured around P0/P1/P2 milestones (headers & SSE handling, health scoring, regression suite, vendor engagement).

4. **GPT-5 Pro incident report obtained (`docs/PROVIDER_ISSUES_PLANNER_RESPONSE.md`)**
   - 20+ page deep dive enumerating root causes (SSE parsing, CDN interstitials, route capability variance) and recommended mitigations.

5. **Configs tweaked**
   - `agent_configs/opencode_grok4fast_c_fs_v2.yaml` now enforces `plan_turn_limit: 1` and allows multiple tool calls per turn.
   - Similar plan-limit behaviour triggered dynamically for GLM config via runtime metadata.

---

## 3. Architecture overview / key subsystems

### 3.1 Agent loop (entry)
- **File**: `agentic_coder_prototype/agent_llm_openai.py`
- **Responsibilities**: compile prompts, select tools/dialects, run iterative main loop, dispatch tool calls, detect completion, persist IR + snapshot.
- **Hot spots**:
  - `_setup_tool_prompts`: now increments `plan_turns`, disables plan mode when limit reached.
  - `_run_main_loop`: sets completion summaries, handles provider errors (returns `provider_error` blocks).
  - `_invoke_runtime_with_streaming`: toggles streaming fallback when provider rejects SSE.
- **Next changes**: integrate SSE parser, branch on content types, tie into health scoring.

### 3.2 Provider runtime layer
- **File**: `agentic_coder_prototype/provider_runtime.py`
- `_decode_body_text` / `_call_with_raw_response` gather raw payloads, handle HTML detection, and classify retry outcomes.
- Runtimes: `OpenAIChatRuntime`, `OpenAIResponsesRuntime`, `OpenRouter` variants.
- Pending: Accept header enforcement, SSE parsing, fallback routing logic.

### 3.3 Logging V2
- Directory structure under `logging/<timestamp>_agent_ws_*`.
- Meta: `meta/conversation_ir.json` (IR snapshots), `errors/turn_*.json` (provider errors), `raw/` directories for request/response dumps.
- To expand: capture HTTP headers and base64 body segments per GPT recommendation.

### 3.4 Config system
- YAMLs under `agent_configs/`; V2 configs support `extends`, `prompts`, `loop` sequences.
- `loop.plan_turn_limit` introduced (P0 guardrail). Ensure new configs set this to avoid infinite plan loops.

### 3.5 Support utilities
- `docs/PROVIDER_FIXES_V2_PLAN.md` — working roadmap.
- `docs/PROVIDER_ISSUES_PLANNER_RESPONSE.md` — GPT-5 analysis.
- `docs/phase_2/HANDOFF_V12.md` — prior state.

---

## 4. Problems solved so far (Phase 2 highlights)
- Structured IR and per-run conversation snapshots.
- Provider capability matrix and metadata tracking.
- Retry/backoff instrumentation for HTML payloads (now extended with raw body capture).
- Plan/build template enforcement (TPSL) and Makefile guards.
- Mock provider for offline diff validation.

Refer to V12 for full enumerations; V13 builds atop these achievements.

---

## 5. Current direction (Phase 2 continuation)
1. **Stabilize OpenRouter provider pipeline** — implement GPT-5’s guardrails (headers, streaming handling, fallback routing).
2. **Instrument provider health** — capability probes, health scoring, circuit breakers.
3. **Enhance observability** — raw payload logging, dashboards, automated regression matrix.
4. **Vendor engagement** — prepare evidence and outreach packages once guardrails deployed.
5. **Continue IR/lifecycle expansion** — integrate run termination metadata (already in place) into analytics once provider issues resolved.

---

## 6. Outstanding issues / known obstacles
- OpenRouter routes returning non-JSON (likely HTML/SSE mishandled) despite HTTP 200.
- DeepSeek route stalling post-first-turn.
- Plan/build transitions need safeguards to avoid infinite plan loops (initial guardrail added; confirm across configs).
- Logging lacks header capture and true raw byte storage; essential for vendor tickets.
- No automated health scoring/circuit breaker yet.
- Regression coverage for multi-provider streaming is missing.

---

## 7. Immediate next steps (P0 milestone — see plan doc)
1. **Headers & content negotiation**
   - Enforce `Accept` and `Accept-Encoding` for OpenRouter; branch logic on `Content-Type`.
2. **Streaming guardrail**
   - Disable streaming for tool turns until SSE path validated; fallback to non-stream or alternate `response_format` on failure.
3. **SSE parsing**
   - Implement SSE parser path; handle `text/event-stream` by reading chunks and reconstructing JSON events.
4. **Logging upgrades**
   - Capture response headers, store raw bytes (base64), add `raw_excerpt/html_excerpt` to error artifacts.
5. **Retry + fallback**
   - On non-JSON detection, retry once with jitter, then reroute to alternate provider; mark route degraded.

See `docs/PROVIDER_FIXES_V2_PLAN.md` for owner placeholders and timing.

---

## 8. Longer-term roadmap (P1/P2 milestones)
- Capability probing on startup; store route profiles.
- Health scoring + circuit breakers for providers.
- Response normalizer unifying JSON/SSE into consistent event stream.
- Request recorder snapshots for incidents (headers, partial body, request ID).
- Regression matrix covering provider × streaming × tool combinations; nightly runs.
- Monitoring/alert dashboards with alert thresholds (HTML rate, stall duration, circuit state).
- Vendor escalation kit per GPT-5 report.
- Runbook and documentation updates.

---

## 9. Verification & testing guidance
- **Unit/regression**: run `pytest tests/test_provider_ir.py -q` and `tests/test_prompt_compiler_v2.py -q` (already clean post-instrumentation).
- **Mock smoke test**: `AGENT_SCHEMA_V2_ENABLED=1 RAY_SCE_LOCAL_MODE=1 python -u main.py agent_configs/opencode_mock_c_fs.yaml --schema-v2 --task implementations/test_tasks/universal_agentic_debugging_task.md --max-iterations 3` — ensures baseline pipeline unaffected.
- **Provider smoke tests** (post-fix): re-run GLM/Grok/DeepSeek C proto tasks; inspect `meta/conversation_ir.json` and `errors/` for absence of provider errors.
- **Regression matrix**: to be implemented (P2.1).

---

## 10. Key commands & scripts
- **Run demo**: `python -u main.py <config> --schema-v2 --task <task> --max-iterations N`.
- **Log reducer**: `python scripts/log_reduce.py logging/<dir> --turn-limit 10 --tool-only --skip-tree` — review conversation summary quickly.
- **Ray test placeholder**: `scripts/run_ray_session_tests.sh` — ensure provider flows once sandbox limitations lifted.

---

## 11. Document references
- `docs/phase_2/HANDOFF_V12.md` — prior handoff.
- `docs/PROVIDER_ISSUES_PLANNER_RESPONSE.md` — GPT-5 incident report.
- `docs/PROVIDER_FIXES_V2_PLAN.md` — implementation plan.
- `docs/PROVIDER_ISSUES_MODEL_REQUEST.md` — original request sent to GPT-5 (for context).

---

## 12. Open questions / decisions needed
1. Do we adopt direct vendor APIs (bypass OpenRouter) as fallback? Requires leadership decision.
2. Storage/compliance requirements for raw response payloads (some may contain sensitive content).
3. Resource allocation for SSE parser/response normalizer (ownership TBD).
4. Timeline for enabling circuit breakers in production (straddles runtime + ops teams).

---

## 13. Suggested onboarding path for incoming engineer
1. Read this Handoff V13 top-to-bottom.
2. Skim `docs/PROVIDER_ISSUES_PLANNER_RESPONSE.md` to grok root cause landscape.
3. Review `docs/PROVIDER_FIXES_V2_PLAN.md` for actionable tasks and assign yourself P0 items.
4. Dive into:
   - `agentic_coder_prototype/agent_llm_openai.py` — understand plan limiting and error handling expansions.
   - `agentic_coder_prototype/provider_runtime.py` — plan SSE/headers modifications.
   - `agent_configs/opencode_*` — confirm plan turn limits and provider-specific overrides.
5. Inspect latest logs in `logging/20251010-*`; run `log_reduce.py` to internalize failure modes.
6. Start implementing P0 guardrails (headers, streaming fallback, logging). Coordinate with ops for credentials and rate limits.
7. Once P0 validated, progress through P1/P2 per plan.

Welcome aboard; this document plus the referenced plan/report should supply all necessary context to continue Phase 2 without institutional knowledge.
