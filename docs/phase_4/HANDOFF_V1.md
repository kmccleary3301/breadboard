# Handoff V1 — KyleCode Phase 4 Continuation Guide

> **Agentic Coder Quick Map.** Start here before spelunking. The files below capture the critical control flow and instrumentation added in Phase 4; line references point directly to the new logic.
>
> | Area | Path | Why it matters | Key Sections / Line Anchors |
> | --- | --- | --- | --- |
> | Probe & Health Entry Point | `agentic_coder_prototype/agent_llm_openai.py` | Main loop now seeds capability probes, logs diagnostics, and consults route health for circuit-breaking. | `_ensure_capability_probes()` (~1740), `_invoke_runtime_with_streaming()` (~1870), `_retry_with_fallback()` (~1790) |
> | Capability Probes | `agentic_coder_prototype/provider_capability_probe.py` | Defines the startup probe runner—stream/tool/json detection, logging, wiring into session metadata. | `_probe_model()` (~90), `CapabilityProbeResult` |
> | Route Health | `agentic_coder_prototype/provider_health.py` | Rolling failure counters + cooldown-based circuit breaker; integrated with routing decisions. | `RouteHealthManager.record_failure()`, `is_circuit_open()` |
> | Response Normalizer | `agentic_coder_prototype/provider_normalizer.py` | Emits canonical event stream (text/tool/finish) behind feature flag for IR/logging parity. | `normalize_provider_result()` |
> | Provider Runtime Enhancements | `agentic_coder_prototype/provider_runtime.py` | Accept headers, SSE parsing, base64 capture—foundation for probes/health metrics. | `_call_with_raw_response()` (~500), `_parse_sse_chat_completion()` |
> | Unit Regression | `tests/test_provider_capability_probe.py`, `tests/test_provider_health.py`, `tests/test_provider_normalizer.py`, `tests/providers/test_provider_runtime_openrouter.py` | Fast validation of new subsystems. | Entire file (each is focused + short) |
> | P0 Reference | `docs/phase_2/HANDOFF_V13.md`, `docs/PROVIDER_FIXES_V2_PLAN.md` | Historical context & backlog; Phase 4 extends these. | Read Sections 2–5 |

---

## 1. Project Recap (KyleCode in Phase 4 Context)

KyleCode is an experimentation platform for agentic coding workflows. It orchestrates planning, tool execution, and code synthesis across multiple LLM providers with an emphasis on:

1. **Provider Agnosticism** — swap runtimes (OpenAI, OpenRouter, Anthropic) without altering executor logic.
2. **Telemetric Richness** — capture IR events, tool transcripts, raw requests/responses, and runtime diagnostics for post-run analysis.
3. **Filesystem Benchmarks** — run “C filesystem” style tasks deterministically via sandboxed toolchains.
4. **Operational Resilience** — fallback routing, retries, vendor escalation packages, circuit-breakers.

Phase 2 (documented in `docs/phase_2/HANDOFF_V13.md`) delivered IR v1, improved logging, and the first guardrails. Phase 3 (ongoing in the repo history) hardened streaming and HTML detection. **Phase 4 extends that work** by layering dynamic capability detection, per-route health management, and a normalized event pipeline to simplify downstream analytics.

---

## 2. What’s Been Done Since Phase 2/3 Handoffs

### 2.1 Capability Probes (P1.1)
* Implemented `ProviderCapabilityProbeRunner`:
  * Executes at conductor startup (behind `provider_probes.enabled` in config, overridable by `KC_DISABLE_PROVIDER_PROBES`).
  * Issues lightweight diagnostic calls per configured model (streaming yes/no, tool-call viability, bare JSON response compliance).
  * Persists results to `meta/capability_probes.json` and session metadata for reuse and debug visibility.
* Added unit coverage in `tests/test_provider_capability_probe.py`.

### 2.2 Route Health & Circuit Breakers (P1.2)
* Added `RouteHealthManager` with:
  * Rolling failure window (`FAILURE_WINDOW_SECONDS` default 10 min).
  * Automatic circuit open after N failures (default 3) with cooldown (15 min).
  * Integration into `_invoke_runtime_with_streaming` and `_retry_with_fallback` to skip unhealthy routes early and record success/failure metadata.
* Health snapshots exposed via `session_state.provider_metadata["route_health"]` for IR export and future dashboards.
* Tests in `tests/test_provider_health.py` ensure pruning/circuit behavior.

### 2.3 Response Normalizer v0.1 (P1.3)
* Introduced `provider_normalizer.normalize_provider_result()` producing canonical `text`, `tool_call`, `finish_reason`, and `finish` events.
* Enabled via feature flag `features.response_normalizer` to avoid disrupting existing consumers.
* Events stored in result metadata (`normalized_events`) and optional JSON dump per turn.
* Validated by `tests/test_provider_normalizer.py`.

### 2.4 P0 Guardrails (Completed Prior But Relevant)
* Accept/Accept-Encoding enforcement in OpenRouter calls (`provider_runtime.py`).
* Streaming policy gating in conductor to disable `stream=true` on OpenRouter tool turns.
* SSE parsing path for `text/event-stream`, base64 capture, HTML classification, and raw excerpts.
* Automatic retry + fallback routing (one jittered retry + alternate model) with diagnostics.
* Artifact logging extended to headers, base64 bodies, and IR attachments.

### 2.5 Tests & CI Surface
* Focused suites added/updated:
  * `tests/providers/test_provider_runtime_openrouter.py` — header injection, SSE parsing, fallback gating.
  * `tests/test_stream_policy.py`, `tests/test_error_artifacts.py`, `tests/test_agent_openai_tools_integration.py` — cover conductor-level behaviors.
* Quick-run battery (`pytest tests/test_provider_capability_probe.py ...`) executes in <2s, aiding smoke validation during development.

---

## 3. Problems Addressed

1. **Unknown Provider Capabilities**  
   * Prior runs entered tool turns with streaming enabled by default, causing repeated SSE decode failures. The probe runner now detects per-route streaming/tool/json compliance before entering the main loop.

2. **Blind Routing to Degraded Routes**  
   * Repeated HTML/non-JSON responses triggered manual fallbacks. The RouteHealthManager automatically opens a circuit after repeated failures, preventing further attempts and triggering a fallback before the first request.

3. **Inconsistent Event Streams**  
   * Mixing SSE and JSON results produced irregular transcripts. The response normalizer ensures future analytics (and IR v2) can rely on consistent event shapes.

4. **Insufficient Diagnostics for Vendor Escalation**  
   * Base64-captured bodies, normalized events, request/response header logging, and health metadata collectively provide richer incident bundles.

---

## 4. Current Direction of Development

Phase 4 shifts from reactive guardrails to **proactive health management** and **observability standardization**. The near-term focus:

1. Finalize P1 deliverables (structured request recorder, integrating probe/health data into routing config & IR).
2. Kick off P2 once P1 stabilizes—device automated regression matrix, monitoring dashboards, and vendor escalation kits using the richer telemetry.
3. Align configuration ergonomics (YAML knobs for health-aware routing, plan toggles, etc.) to reduce manual tweaks.

---

## 5. Remaining Work (Prioritized Backlog)

### 5.1 P1 Outstanding Tasks

1. **P1.4 Structured Request Recorder**  
   * Extend `LoggerV2` and conductor pipeline to store sanitized request headers + payload metadata for all provider calls (currently only response artifacts are captured).  
   * Provide CLI helper (e.g., `scripts/log_reduce.py` extension) to bundle request/response artifacts for vendor handoff. Ensure API keys are redacted while retaining request IDs and essential debugging info.

2. **P1.5 Routing Integration & Documentation** — ✅ Completed  
   * Streaming/tool decisions now consult capability probe metadata each turn (auto-disabling streaming on failed probes and switching native tools off when tool probes fail).  
   * New YAML block `providers.models[].routing` drives probe-aware overrides and prioritized fallback routes; history is exposed via IR `reasoning_meta` diagnostics.  
   * Fallback selection skips unhealthy circuits and records decisions to Markdown/IR for downstream analytics.  
   * Docs/runbooks updated with the new knobs (see Section 6.1 + config examples).

### 5.2 P2 Preparation

Although not immediately actionable, keep these in mind:

1. **Regression Matrix (P2.1)** — orchestrate provider × streaming × tool combinations in nightly tests using the capability probe outputs as baselines. (See `tests/test_provider_regression_matrix.py` and `scripts/run_regression_matrix.py` for the automation harness.)
2. **Monitoring/Alerting (P2.2)** — instrument metrics (non-JSON rate, circuit states, probe success) once structured request recorder lands. (`meta/provider_metrics.json` and `meta/telemetry.jsonl` capture per-route latency, overrides, and fallback counters; see `docs/phase_4/TELEMETRY_METRICS.md`.)
3. **Vendor Escalation Kit (P2.3)** — transform the richer artifacts into templated bundles for OpenRouter and upstream vendors. (`scripts/build_vendor_bundle.py logging/<run>` creates a sanitized ZIP with capability probes, structured requests, metrics, and error payloads.)

---

## 6. Implementation Notes & Deep Dive

### 6.1 `agentic_coder_prototype/agent_llm_openai.py`
* **Startup Flow**  
  * Constructor seeds `RouteHealthManager` and `ProviderCapabilityProbeRunner`. The latter runs once per session when `provider_probes.enabled` is true.
* **Main Loop Updates**  
  * `_ensure_capability_probes()` attaches results to session metadata and logs to Markdown transcripts.
* **Streaming Invocation**  
  * `_invoke_runtime_with_streaming()` now:
    * Checks circuit breaker state before issuing a call (immediate fallback if open).
    * Records successes/failures in `RouteHealthManager`.
    * Persists health snapshots for analytics.
    * Annotates warnings/diagnostics to transcript + Markdown log.
    * On fallback success, records degradations and fallback metadata.
* **Retry & Fallback**  
  * `_retry_with_fallback()`:
    * Jittered same-route retry with success/failure recording.
    * Fallback route chosen based on provider (currently simple best-effort mapping; TODO align with capability probes for more nuanced selection).
    * Health metadata updated after each attempt.
* **Response Normalization**  
  * When `features.response_normalizer` is truthy, normalized events are stored per turn.

### 6.2 `agentic_coder_prototype/provider_capability_probe.py`
* Probes check:
  * `stream=true` viability.
  * Tool-execution success (currently via simple assistant response; future work may add actual tool schema).
  * JSON compliance (basic heuristics; refine as needed).
* Output persisted to both V2 logs and session metadata.
* Hooked via `KC_DISABLE_PROVIDER_PROBES` environment guard to disable cheaply for testing.

### 6.3 `agentic_coder_prototype/provider_health.py`
* Maintains per-route deque of timestamps (success/failure).
* `record_failure()` opens circuit when threshold met.
* `record_success()` clears circuit if cooldown expired.
* TODO: make thresholds configurable via YAML/env (track for P2).

### 6.4 `agentic_coder_prototype/provider_normalizer.py`
* Stateless conversion from `ProviderResult` to canonical event list.
* Current payload minimal; future P2 tasks can enrich with reasoning traces or streaming deltas.

### 6.5 `agentic_coder_prototype/provider_runtime.py`
* Accept header & SSE decoding integrated (see P0).
* `_call_with_raw_response()` now:
  * Injects Accept headers.
  * Handles `text/event-stream` via `_parse_sse_chat_completion`.
  * Captures base64 body even on errors.

---

## 7. Configuration & Environment Considerations

1. `provider_probes.enabled` controls capability probing per config. Place under root YAML (e.g. `agent_configs/*`).
2. `features.response_normalizer` toggles normalized event emission. Start with selective enabling to validate downstream consumers.
3. `KC_DISABLE_PROVIDER_PROBES=1` disables probes for local testing or rate-limited environments.
4. Circuit-breaker thresholds currently static; plan to expose via config soon.

---

## 8. Testing & Validation Recipes

* **Unit/Functional**  
  * `pytest tests/test_provider_capability_probe.py tests/test_provider_health.py tests/test_provider_normalizer.py -q`
  * `pytest tests/providers/test_provider_runtime_openrouter.py -q`
  * `pytest tests/test_agent_openai_tools_integration.py -q`
* **Integration Smoke** (post-P1)
  * Run `main.py` with OpenRouter configs, confirm `meta/capability_probes.json` populated, logs show circuit transitions.
  * Inspect `session_state.provider_metadata` snapshots for `route_health`, `capability_probes`, `fallback_route`.
* **Manual Inspection**  
  * Check `logging/<run>/meta/capability_probes.json`.
  * Review `logging/<run>/conversation/conversation.md` for `[provider-retry]` and `[circuit-open]` markers.
  * Evaluate `route_health` metadata in IR to confirm circuit reopen after cooldown.

---

## 9. Documentation & Runbooks

* Update `docs/PROVIDER_FIXES_V2_PLAN.md` to reflect completed P1 items and add acceptance criteria for P1.4/P1.5.
* Draft structured request recorder spec (fields, redaction rules) before implementing to avoid rework.
* Extend runbook with:
  * Steps to interpret capability probe output.
  * Interpreting `route_health` and how to manually clear circuits if needed.
  * Toggle instructions for response normalizer when analyzing old vs new runs.

---

## 10. Immediate Next Steps for Incoming Engineer

1. **Confirm Probe + Health Telemetry**  
   * Run a short local session (`KC_DISABLE_PROVIDER_PROBES=0 python -u main.py ...`) to confirm capability probes/logs and inspect `route_health` metadata.
2. **Implement Structured Request Recorder (P1.4)**  
   * Extend `LoggerV2` to store sanitized headers + request payload metadata (preferring JSON).
   * Update conductor to call new recorder hooks.
   * Provide targeted tests analogous to `tests/test_error_artifacts.py`.
3. **Validate Capability/Health Routing (P1.5)**  
   * Ensure active configs define `providers.models[].routing` overrides for priority routes.
   * Spot-check IR/Markdown logs for the new `stream_policy` and `fallback_route` diagnostics.
4. **Deprecate Temporary Fixtures**  
   * Some tests stubbed minimal router/registry (see `tests/test_agent_openai_tools_integration.py`); expand once configs finalize.

Once P1 closes, transition to P2 tasks: regression matrix, monitoring, vendor escalation assets, and documentation refresh.

---

## 11. Reference Materials

* `docs/phase_2/HANDOFF_V13.md` — prior handoff, deep dive on IR v1/logging foundations.
* `docs/PROVIDER_FIXES_V2_PLAN.md` — master plan with P0/P1/P2 milestones; update as tasks complete.
* `docs/PROVIDER_ISSUES_PLANNER_RESPONSE.md` — GPT-5 Pro analysis providing recommended mitigations (SSE handling, compression, circuit breakers).
* `logging/<timestamp>` directories — real run artifacts to observe how the new instrumentation behaves.

---

## 12. Closing Notes

* The repository currently contains uncommitted application/config changes unrelated to Phase 4 (see `git status`). Avoid overwriting unless coordinated.
* Keep probing/probing output lightweight—monitor startup latency and consider caching results if necessary.
* Maintain the culture of test-first instrumentation; every new subsystem added in Phase 4 has accompanying pytest coverage. Continue this discipline for P1.4/P1.5 and beyond.

Welcome aboard—this document, combined with the referenced files, should let you resume development without spelunking the entire codebase. The priority is finishing P1, then accelerating into observability and vendor-engagement tooling for Phase 2.
