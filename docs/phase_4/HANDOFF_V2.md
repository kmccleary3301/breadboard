# Handoff V2 — KyleCode Phase 4 & P2 Completion Guide

> **Quick Navigation (Read Me First).** The table below highlights the must-read files before diving into the code. This saves you from scanning the entire repo and gives you the decisive context fast.
>
> | Area | File(s) | Why It Matters | Key Sections / Anchors |
> | --- | --- | --- | --- |
> | Project Overview & Status | `docs/phase_4/HANDOFF_V2.md` (you're here) | Comprehensive map of what’s been built, why it matters, and what’s next. | Entire document. |
> | Phase 4 History | `docs/phase_4/HANDOFF_V1.md` | First-phase recap: capability probes, route health, normalizer. | Sections 2–6. |
> | Provider Plan | `docs/PROVIDER_FIXES_V2_PLAN.md` | Master list of P0/P1/P2 work; now marked with ✅ for completed items. | P2 milestone table. |
> | Completion Strategies Research | `docs/research/AGENT_COMPLETION_NOTES.md` | Comparison across Codex CLI, Claude Code, Cursor, OpenCode for task completion. | Entire file. |
> | Runbook | `docs/phase_4/runbook.md` | Operational guide: environment setup, regression matrix, telemetry export, incident checklist. | Sections 2–7. |
> | Telemetry & Metrics | `docs/phase_4/TELEMETRY_METRICS.md` | How provider metrics are emitted (`meta/provider_metrics.json`, JSONL). | How to export & integrate. |
> | Regression Automation | `tests/test_provider_regression_matrix.py`, `scripts/run_regression_matrix.py` | Harness for P2.1 regression matrix. | Scenario definitions & script usage. |
> | Metrics Collector | `agentic_coder_prototype/provider_metrics.py`, `agentic_coder_prototype/agent_llm_openai.py` | Where per-route metrics are captured and logged. | `ProviderMetricsCollector`, `_invoke_runtime_with_streaming`, `_retry_with_fallback`, `finalize_run`. |
> | Agent Configuration | `agent_configs/*` (esp. `opencode_openrouter_glm46...`, `opencode_openrouter_deepseek...`) | Shows routing overrides, completion settings, prompts guiding current regressions. | Look at `providers.models[].routing`, `completion`, new `prompts.task_prompt`. |
> | Regression Prompt | `docs/regression_prompts/enumerate_workspace.md` | Canonical prompt used for enumeration regression tests. | Entire file. |
> | Logging Scripts | `scripts/build_vendor_bundle.py`, `scripts/export_provider_metrics.py` | Tools for incident bundles and telemetry export. | CLI usage. |

---

## 1. Project Recap — KyleCode in Context

### 1.1 What is KyleCode?
KyleCode is an agentic coding platform specializing in reproducible filesystem tasks (“C filesystem” style benchmarks). It orchestrates planning, tool execution, and coding across multiple LLM providers, with emphasis on:

* **Provider Agnosticism.** Pluggable runtimes (OpenAI, OpenRouter, Anthropic, mock providers).
* **Resilience.** Capability probes, circuit breakers, and fallbacks keep tasks running even when a provider misbehaves.
* **Observability.** Structured logs, telemetry, and vendor bundles make it easy to debug incidents and collaborate with providers.
* **Determinism.** Tight guardrails (sandbox, tool policies, completion detector).

### 1.2 Phase History
* **Phase 2.** Introduced Schema V2 configs, completion detector, logging revamp (see `docs/phase_2/FULL_EXPLANATION_SPEC.md`, `HANDOFF_V13`).
* **Phase 3.** Hardened streaming, HTML detection, error artifacts (not redocumented here but present in repo history).
* **Phase 4 (P1).** Capability probes, route health manager, response normalizer, structured request recorder (documented in `HANDOFF_V1.md`).
* **P2 (current handoff).** Regression matrix harness, telemetry export, vendor bundles, runbooks—forged to prevent regressions and accelerate incident response.

### 1.3 Why the Upgrade?
OpenRouter-backed models (GLM‑4.6, DeepSeek v3.2 exp, etc.) were returning HTML or stalling, breaking tool turns. The Phase 4 backlog implemented guards to detect non-JSON payloads, circuit-break failing routes, and fall back to direct OpenAI models. Along the way we layered telemetry, structured requests, and regression tests so each release can be validated quickly.

---

## 2. What’s Done (Phase 4 + P2)

### 2.1 Phase 4 P1 Summary (recap from V1)
1. **Capability Probes.** `ProviderCapabilityProbeRunner` runs at session start to test stream/tool/json compatibility. Results stored in `meta/capability_probes.json`.
2. **Route Health & Circuit Breakers.** `RouteHealthManager` tracks repeated failures per route; state is written to session metadata and logs.
3. **Response Normalizer.** `provider_normalizer.py` produces canonical text/tool/finish events when the feature flag is enabled.
4. **Structured Request Recorder.** `logs/meta/requests/*.json` captures sanitized headers + body excerpts for each call.

### 2.2 P2 Achievements
| Milestone | Result | Artefacts |
| --- | --- | --- |
| P2.1 Regression Matrix | Harness + script run the key provider scenarios (mocking OpenRouter issues) | `tests/test_provider_regression_matrix.py`, `scripts/run_regression_matrix.py` |
| P2.2 Telemetry Pipeline | `ProviderMetricsCollector` logs per-route stats to `meta/provider_metrics.json` & telemetry JSONL; exporter forwards events | `agentic_coder_prototype/provider_metrics.py`, `scripts/export_provider_metrics.py` |
| P2.3 Vendor Bundles | Script packages capability probes, structured requests, metrics, raw excerpts, conversation log into a ZIP | `scripts/build_vendor_bundle.py` |
| P2.4 Runbooks & Docs | OPs/incident guide created | `docs/phase_4/runbook.md`, `docs/phase_4/TELEMETRY_METRICS.md`, updates to `docs/PROVIDER_FIXES_V2_PLAN.md` |
| P2.5 Direct Vendor Prep | Configs prefer direct OpenAI as fallback; telemetry makes results comparable | `agent_configs/base_v2.yaml`, `opencode_openrouter_*`, handoff doc |

### 2.3 Telemetry & Logging Enhancements
* `Meta/provider_metrics.json` — aggregated metrics by route (latency, errors, overrides, fallbacks).
* `Meta/telemetry.jsonl` — JSONL events including `provider_metrics`, ready for ingestion (default file path if `RAYCODE_TELEMETRY_PATH` unset).
* `Structured requests` — sanitized request payloads per turn.
* `Scripts/export_provider_metrics.py` — prints/POSTs telemetry events.
* `Scripts/build_vendor_bundle.py` — bundles run artifacts for vendor escalation.

### 2.4 Regression Prompt & Configs
* `docs/regression_prompts/enumerate_workspace.md` — canonical prompt instructing the agent to enumerate workspace and call `mark_task_complete()`.
* Updated configs (GLM-4.6, DeepSeek v3.2 exp) now:
  * Reference the prompt via `prompts.task_prompt`.
  * Enable `completion.tool_finish`, `text_sentinels`, `provider_signals`.
  * Include fallback models (`openai/gpt-4o-mini`) with prioritized routing.

---

## 3. Key Files & Architectural Tour

### 3.1 Core Execution Flow
* `agentic_coder_prototype/agent_llm_openai.py`
  * **Constructor:** sets up logger, capability probes, route health, metrics collector.
  * **`run_agentic_loop`:** orchestrates config loading, prompt assembly, main loop.
  * **`_invoke_runtime_with_streaming`:** streaming logic, circuit breaker checks, metrics capture.
  * **`_retry_with_fallback`:** same-route retry + fallback selection; updates metrics/logging.
  * **`finalize_run`:** persists provider metrics, telemetry events, IR, and completion summary.
  * **Telemetry Hooks:** `self.provider_metrics.add_call(...)` is invoked for primary and fallback routes.
* `agentic_coder_prototype/provider_metrics.py`
  * `ProviderMetricsCollector` class records per-call metrics, fallback/circuit events, overrides.
  * `snapshot()` returns aggregated data used by logs and telemetry.

### 3.2 Configuration & Prompts
* `agent_configs/base_v2.yaml` — schema V2 baseline; note the new direct OpenAI model entry and routing block.
* `agent_configs/opencode_openrouter_glm46_c_fs_perturn_append.yaml` — main config used for GLM 4.6 testing; contains fallback, completion, prompts.
* `docs/regression_prompts/enumerate_workspace.md` — ensures regressions use a deterministic prompt.
* `docs/research/AGENT_COMPLETION_NOTES.md` — research summary on completion strategies across other platforms.

### 3.3 Logging & Scripts
* `scripts/run_regression_matrix.py` — single command to run regression harness with correct env variables.
* `scripts/export_provider_metrics.py` — parse telemetry JSONL and print aggregated metrics or POST to an endpoint.
* `scripts/build_vendor_bundle.py` — gather everything needed for vendor support.
* `docs/phase_4/runbook.md` — environment setup, log interpretation, incident workflow, metrics export, vendor bundle, optional direct API activation.

### 3.4 Regression Tests & Harness
* `tests/test_provider_regression_matrix.py` — two scenario definitions (streaming fallback, text-only) that assert stream policies, fallback events, and metrics snapshot contents.
* `docs/phase_4/regression_matrix_plan.json` — JSON outline of scenarios for future expansion.

---

## 4. Solved Problems (and How)

### 4.1 OpenRouter HTML/Non-JSON Response Failures
* Content-Type check in `provider_runtime._call_with_raw_response()` rejects non-JSON responses and retries with fallbacks.
* Logs now capture base64 bodies + HTML snippets for debugging.
* Fallback logic ensures tasks continue via direct OpenAI models if the route fails repeatedly.

### 4.2 Lack of Dynamic Capability Awareness
* `ProviderCapabilityProbeRunner` runs automatically, storing stream/tool/json support per model in metadata and logs.
* Configs can reference this data to disable streaming/tools per route when probes fail.
* Telemetry captures stream/tool override events for monitoring.

### 4.3 No Systematic Regression Coverage
* Regression harness (`tests/test_provider_regression_matrix.py` + script) covers provider × streaming × tool combinations.
* Works offline via mocked OpenRouter to reproduce failure modes rapidly.
* Provides a blueprint for expanding test cases to real provider calls.

### 4.4 Incident Response Friction
* Structured request recorder + vendor bundle script + telemetry exporter make it fast to compile incident evidence.
* `docs/phase_4/runbook.md` details the exact commands to capture artifacts and escalate to vendors.

### 4.5 Telemetry Visibility
* Per-run metrics are now stored both in `provider_metrics.json` and telemetry JSONL.
* Export script provides aggregated summary or direct ingestion into external systems.
* Metrics cover call counts, errors, HTML occurrences, fallback usage, stream/tool overrides.

---

## 5. Current Challenges & Observations

### 5.1 Completion Behaviour
* The regression prompt instructs the agent to call `mark_task_complete()`, but actual runs with OpenRouter GLM-4.6 still end due to `max_steps_exhausted`.
* Native tools may not be supported for certain OpenRouter routes; the fallback direct OpenAI route responds correctly but the agent repeats enumeration commands.
* Completion detector has text sentinel support (`TASK COMPLETE`) but the model doesn’t produce it; highlight this as a behaviour gap for next steps.

### 5.2 Real Provider Verification
* Real GLM-4.6 / DeepSeek runs (no mocks) execute without transport errors, but they do not complete tasks automatically.
* Logging confirms fallback metrics, but manual review shows the agent loops through enumerations; fine-tuning prompt plan or improving completion detection is the next frontier.

### 5.3 Monitoring Integration
* Telemetry JSONL exists locally; to integrate with production monitoring, you’ll need to run `scripts/export_provider_metrics.py` periodically or set `RAYCODE_TELEMETRY_PATH` to a central location.
* Build dashboards/alerts around the summary provided (calls, errors, HTML rate, overrides).

---

## 6. What’s Next

### 6.1 High-Priority Next Steps (Short Term)
1. **Completion Mechanics**
   * Ensure `mark_task_complete` is available in tool schema (native vs text) and adjust prompts so the agent triggers completion within allowed steps.
   * If the provider doesn’t support native tools, adjust completion detector to treat “summary + sentinel” as finishing conditions, or run a post-processing step that closes the task when the output meets the plan criteria.
2. **Regression Harness Enhancements**
   * Add real provider scenarios (possibly optional/flagged) to confirm the pipeline works end-to-end.
   * Expand `REGRESSION_SCENARIOS` to include DeepSeek-specific cases once completion is reliable.
3. **Telemetry Ingestion**
   * Wire telemetry JSONL into your monitoring solution. Use the exporter to push events to a metrics endpoint or aggregator.

### 6.2 Medium-Term Opportunities
* **Completion Loop Fixes**
  * Investigate prompting strategies or policy adjustments so the model doesn’t spin on enumeration commands.
  * Possibly implement a conductor hook that terminates once a plan is satisfied (e.g., the agent produces bullet summary + `mark_task_complete`).
* **Fallback Metrics**
  * Extend metrics to include latency percentiles, success ratios per fallback route, or provider-specific heuristics (e.g., number of consecutive failures before circuit opens).
* **Vendor Direct API Runs**
  * Evaluate direct OpenAI (or other vendors) beyond fallback—all configs now have fallback entries ready for experiment.

### 6.3 Longer-Term (Future Phases)
* **Automation Integration**
  * Add `scripts/run_regression_matrix.py` and telemetry exporting to CI/nightly tasks (once resource constraints are resolved).
* **Agent Completion Research**
  * Consider adaptation from Codex/Claude practices (e.g., injecting explicit “when done, say DONE”) and track the effect across providers.
* **Tooling Enhancements**
  * Introduce an automated log reducer or a WSGI endpoint to fetch run status, metrics, and bundles on demand.

---

## 7. Operational Runbook Snapshot

### 7.1 Environment Setup
```bash
conda activate ray_sce_test
export AGENT_SCHEMA_V2_ENABLED=1
export KC_DISABLE_PROVIDER_PROBES=0
export OPENROUTER_API_KEY=<your key>
export OPENAI_API_KEY=<your key>
# Optional telemetry sink
export RAYCODE_TELEMETRY_PATH=/var/log/kylecode/provider_metrics.jsonl
```

### 7.2 Run Regression Harness
```bash
python scripts/run_regression_matrix.py
```
Outputs go to console; use `pytest -q` for detailed debug.

### 7.3 Run Manual Task (GLM-4.6 Example)
```bash
conda run -n ray_sce_test python -u main.py \
  agent_configs/opencode_openrouter_glm46_c_fs_perturn_append.yaml \
  --task docs/regression_prompts/enumerate_workspace.md \
  --max-iterations 12
```
Inspect results via `scripts/log_reduce.py logging/<run>`.

### 7.4 Export Telemetry & Build Vendor Bundle
```bash
python scripts/export_provider_metrics.py logging/<run>/meta/telemetry.jsonl --print-summary
python scripts/build_vendor_bundle.py logging/<run>
```

---

## 8. Known Limitations & Behaviour Notes

1. **Completion**: Still reliant on the model to call `mark_task_complete`; fallback direct OpenAI doesn’t do it in the current regression prompt, causing max-steps exhaustion.
2. **Native Tools**: OpenRouter’s alternate routes may ignore native tool calls; expect tool prompts to be textual instructions instead.
3. **Telemetry**: File-based JSONL needs to be collected/shipped; no out-of-box integration into external monitoring.
4. **Regression Prompt**: Works for enumeration but doesn’t prevent repeated commands; consider editing the prompt to mention “Stop after summary”.
5. **CI Integration**: Scripts exist but are not wired into a CI/CD pipeline; do this once resource constraints are addressed.

---

## 9. Appendix

### 9.1 Commands Run During Final Validation
```
python scripts/run_regression_matrix.py
conda run -n ray_sce_test python -u main.py agent_configs/opencode_openrouter_glm46_c_fs_perturn_append.yaml --task docs/regression_prompts/enumerate_workspace.md --max-iterations 12
conda run -n ray_sce_test python -u main.py agent_configs/opencode_openrouter_deepseek_v32exp_c_fs_perturn_append.yaml --task docs/regression_prompts/enumerate_workspace.md --max-iterations 12
scripts/log_reduce.py logging/20251015-021419_agent_ws_opencode --turn-limit 5
scripts/export_provider_metrics.py logging/20251015-021419_agent_ws_opencode/meta/telemetry.jsonl --print-summary
scripts/build_vendor_bundle.py logging/20251015-014722_agent_ws_opencode
```

### 9.2 Remaining TODOs (from conversation)
* Solve completion loop for regression prompt (ensuring final `mark_task_complete` call).
* Evaluate native tool support for OpenRouter GLM-4.6 / DeepSeek (likely unsupported).
* Instrument direct provider prompts to finish without manual intervention, or implement conductor heuristics to auto-complete.

---

**End of Handoff V2.** You now have everything needed—architecture, configs, runbooks, and outstanding tasks—to pick up the project efficiently. Reach out to the telemetry and regression scripts first, confirm the prompts yield completions, and expand the regression coverage as you harden the completion behaviour.
