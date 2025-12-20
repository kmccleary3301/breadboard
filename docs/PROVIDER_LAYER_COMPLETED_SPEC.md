PROVIDER LAYER & IR COMPLETION SPEC
==================================

> **Audience:** Engineers onboarding to BreadBoard provider routing, normalized IR, and regression tooling  
> **Scope:** Final Phase 4 + P2 implementation details, invariants, and validation results  
> **Last Updated:** 2025‑10‑15 (Phase 4 P2 handoff)

TABLE OF CONTENTS
-----------------

1. [Architecture Overview](#1-architecture-overview)  
2. [Provider Abstractions](#2-provider-abstractions)  
   2.1 [Provider Router](#21-provider-router)  
   2.2 [Runtime Context & Adapters](#22-runtime-context--adapters)  
   2.3 [Capability Probes & Route Health](#23-capability-probes--route-health)  
   2.4 [Metrics & Telemetry](#24-metrics--telemetry)  
3. [Intermediate Representation (IR) Layer](#3-intermediate-representation-ir-layer)  
   3.1 [Normalized Event Stream](#31-normalized-event-stream)  
   3.2 [Completion Detector & Summaries](#32-completion-detector--summaries)  
   3.3 [Structured Request Recorder](#33-structured-request-recorder)  
4. [Regression Matrix & Testing Strategy](#4-regression-matrix--testing-strategy)  
   4.1 [Harness Scenarios](#41-harness-scenarios)  
   4.2 [Key Assertions](#42-key-assertions)  
   4.3 [Sample Run Output](#43-sample-run-output)  
5. [Operational Playbook](#5-operational-playbook)  
   5.1 [Configuration Flags](#51-configuration-flags)  
   5.2 [Telemetry Export & Vendor Bundles](#52-telemetry-export--vendor-bundles)  
   5.3 [Failure Triage Checklist](#53-failure-triage-checklist)  
6. [Future Extensions](#6-future-extensions)  
   6.1 [Adaptive Prompting & Dialects](#61-adaptive-prompting--dialects)  
   6.2 [Reward Instrumentation](#62-reward-instrumentation)  
   6.3 [QueryLake & CLI Integration](#63-querylake--cli-integration)


1. Architecture Overview
------------------------

The provider layer mediates all model communication inside BreadBoard. Its responsibilities are:

- **Model-Agnostic Invocation:** Translate unified tool schemas into provider-specific payloads and handle native tool capabilities when available.
- **Resilience & Health:** Probe provider features, record failures, and route calls through fallback chains with circuit breaking.
- **Observability & Replay:** Persist normalized IR events, structured request snapshots, and task-level telemetry for diagnostics or vendor escalation.
- **Testing & Regression:** Reproduce provider failure modes via mocks and monitor guardrail behaviour before integration releases.

The implementation lives primarily in `agentic_coder_prototype/` and is orchestrated by `agent_llm_openai.py`, which wires together routing, probing, telemetry, and the main agent loop.


2. Provider Abstractions
------------------------

### 2.1 Provider Router

`agentic_coder_prototype/provider_routing.py` defines a `ProviderRouter` that maintains per-provider configuration (`ProviderConfig`) and runtime descriptors. The router decides which provider adapter to use for a model ID, applies tool schema translation, and selects base URLs + API keys.

```python
# agentic_coder_prototype/provider_routing.py
class ProviderRouter:
    def __init__(self):
        self.providers = {
            "openai": ProviderConfig(
                provider_id="openai",
                supports_native_tools=True,
                tool_schema_format="openai",
                api_key_env="OPENAI_API_KEY",
                runtime_id="openai_chat",
            ),
            "openrouter": ProviderConfig(
                provider_id="openrouter",
                supports_native_tools=True,
                base_url="https://openrouter.ai/api/v1",
                api_key_env="OPENROUTER_API_KEY",
                default_headers={
                    "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", ""),
                    "X-Title": os.getenv("OPENROUTER_APP_TITLE", "Ray SCE Agent"),
                    "Accept": "application/json; charset=utf-8",
                    "Accept-Encoding": "identity",
                },
            ),
            # Anthropic and mock entries omitted for brevity
        }
```

Key invariants:

- Every provider has a deterministic tool schema transformation (`OpenAIToolTranslator`, `AnthropicToolTranslator`) to keep internal tool definitions agnostic.
- Route selection is driven by agent config YAML, allowing per-model fallbacks (e.g., OpenRouter → direct OpenAI).

### 2.2 Runtime Context & Adapters

Adapters live under `agentic_coder_prototype/provider_adapters.py` and expose a consistent `invoke()` signature. The runtime context (`ProviderRuntimeContext`) holds metadata such as `fallback_of`, `stream`, and `session_id`.

Within the conductor (`agent_llm_openai.py`), `_invoke_runtime_with_streaming` orchestrates:

1. Checking route health / circuit breakers.
2. Invoking the provider runtime with streaming when permitted.
3. Recording IR deltas and metrics per chunk or final payload.

```python
# agentic_coder_prototype/agent_llm_openai.py
def _invoke_runtime_with_streaming(self, context, route, messages, tools, stream):
    start = time.perf_counter()
    result = provider_registry.invoke(
        route.runtime_id,
        client=context.client,
        model=route.model_id,
        messages=messages,
        tools=tools,
        stream=stream,
        context=context,
    )
    elapsed = time.perf_counter() - start
    self.provider_metrics.add_call(
        route=route.model_id,
        stream=stream,
        elapsed=elapsed,
        outcome="success",
        html_detected=result.metadata.get("html_detected", False),
    )
    return result
```

### 2.3 Capability Probes & Route Health

- `ProviderCapabilityProbeRunner` runs synthetic tasks at session start to gather tool/stream/json compatibility. Results persist in `session_state.provider_metadata`.
- `RouteHealthManager` maintains failure counters; when thresholds are hit, routes are marked “open” and skipped until a cool-down period elapses. Circuit skips are surfaced in metrics and logged as routing events.

### 2.4 Metrics & Telemetry

`ProviderMetricsCollector` aggregates per-call data, fallback transitions, and override decisions.

```python
# agentic_coder_prototype/provider_metrics.py
collector.add_call(
    route="openrouter/z-ai/glm-4.6",
    stream=False,
    elapsed=1.42,
    outcome="error",
    error_reason="mock streaming failure",
    html_detected=True,
)
```

Snapshots are serialized to `meta/provider_metrics.json` and mirrored to telemetry JSONL for downstream ingestion. This enables post-run analytics on:

- Success/error counts per route.
- HTML detection rates.
- Stream/tool override frequencies.
- Fallback chains (primary → secondary route transitions).


3. Intermediate Representation (IR) Layer
-----------------------------------------

### 3.1 Normalized Event Stream

The IR layer standardizes provider responses into canonical events so transcripts, logs, and downstream automation can reason about them uniformly.

```python
# agentic_coder_prototype/provider_normalizer.py
def normalize_provider_result(result: ProviderResult) -> List[Dict[str, Any]]:
    events: List[NormalizedEvent] = []
    for idx, message in enumerate(result.messages or []):
        if message.content:
            events.append(NormalizedEvent(
                type="text",
                payload={"message_index": idx, "role": message.role, "content": message.content},
            ))
        if message.tool_calls:
            for call_idx, tool_call in enumerate(message.tool_calls):
                events.append(NormalizedEvent(
                    type="tool_call",
                    payload=_tool_call_to_payload(tool_call, idx, call_idx),
                ))
        if message.finish_reason:
            events.append(NormalizedEvent(
                type="finish_reason",
                payload={"message_index": idx, "finish_reason": message.finish_reason},
            ))
    events.append(NormalizedEvent(
        type="finish",
        payload={"usage": result.usage, "metadata": result.metadata, "model": result.model},
    ))
    return [event.to_dict() for event in events]
```

**Why it matters:**

- Supports transcript replay for incident debugging irrespective of provider streaming quirks.
- Enables completion detector and log reducer to operate on a uniform schema.
- Feeds into Markdown transcript writer and vendor bundles as deterministic artifacts.

### 3.2 Completion Detector & Summaries

`CompletionDetector` leverages tool finish hooks (`mark_task_complete`) and text sentinels (`TASK COMPLETE`) to determine task termination. Current limitations (seen in regression runs) are due to providers skipping the sentinel, making this a priority for prompt tuning or heuristic completion.

`SessionState.completion_summary` retains the last known reason (`success`, `max_steps_exhausted`, etc.) for downstream reporting.

### 3.3 Structured Request Recorder

All provider invocations emit sanitized request/response excerpts:

- Headers with OpenRouter request IDs.
- Body prefix (2 KB) for quick triage.
- Metadata linking to model, route, and fallback context.

These artifacts live under `logging/<run>/meta/requests/` and are bundled automatically during incident export.


4. Regression Matrix & Testing Strategy
---------------------------------------

### 4.1 Harness Scenarios

`tests/test_provider_regression_matrix.py` defines deterministic scenarios that model OpenRouter failure modes:

| Scenario ID | Stream | Expected Outcome | Purpose |
|-------------|--------|------------------|---------|
| `openrouter_streaming_fallback` | True | Stream override + fallback triggered | Simulates SSE failure leading to fallback |
| `openrouter_text_only` | False | No fallback, no override | Ensures healthy routes skip guardrails |

Configs force `mock/dev` runtime fallback to guarantee deterministic behaviour when asserting metrics.

### 4.2 Key Assertions

The harness validates:

- Stream policy events are emitted when capability probes fail.
- Fallback routing events are logged and recorded in metrics.
- Error counters increment only when expected.

```python
assert metrics["summary"]["errors"] > 0, "expected error path to be recorded"
assert metrics["fallbacks"], "expected fallback metrics recorded"
```

These assertions harden the guardrails before running live providers.

### 4.3 Sample Run Output

```bash
$ python scripts/run_regression_matrix.py
[regression-matrix] Running /usr/bin/python -m pytest tests/test_provider_regression_matrix.py -q
..                                                                                         [100%]
2 passed in 2.14s
```

Artifacts:

- `logging/<ts>_agent_ws_opencode/meta/provider_metrics.json` – snapshot per run.
- Pytest output summarizing pass/fail status for CI visibility.


5. Operational Playbook
-----------------------

### 5.1 Configuration Flags

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_SCHEMA_V2_ENABLED` | `1` | Enables Phase 2 schema pipeline and completion detector |
| `KC_DISABLE_PROVIDER_PROBES` | `0` | Allow capability probes; set `1` to skip during offline testing |
| `RAY_SCE_LOCAL_MODE` | `0` | Switch conductor to in-process sandbox for testing |
| `OPENROUTER_API_KEY`, `OPENAI_API_KEY` | — | Required for live provider calls |

### 5.2 Telemetry Export & Vendor Bundles

- `scripts/export_provider_metrics.py <telemetry.jsonl>` prints aggregated summaries or pushes to an external sink.
- `scripts/build_vendor_bundle.py <run_dir>` packages transcripts, structured requests, metrics, and IR snapshots for handoff to providers.

These tools were validated during Phase 4 P2 and referenced in the runbook (`docs/phase_4/runbook.md`).

### 5.3 Failure Triage Checklist

1. Inspect `provider_metrics.json` for route-level error spikes.  
2. Review `meta/requests/*.json` for malformed payloads or HTML responses.  
3. Confirm capability probe results; rerun if stale.  
4. If fallback was triggered repeatedly, adjust routing priorities or disable the failing route via config.  
5. Use vendor bundle script to assemble evidence before escalating to providers.


6. Future Extensions
--------------------

### 6.1 Adaptive Prompting & Dialects

Enhanced dialect manager already supports A/B testing, adaptive selection, and per-model profiles. Next steps include:

- Recording per-dialect success metrics (PAS/HMR) to feed adaptive policies.
- Integrating prompt synthesis templates tuned per provider family.

### 6.2 Reward Instrumentation

The Phase 2 AUTO_OPTIM spec outlines dense reward shaping for tool correctness, patch application, LSP impact, and functional outcomes. Implementing these metrics in telemetry will unblock GEPA-driven prompt evolution and hyperparameter search.

### 6.3 QueryLake & CLI Integration

The TypeScript `tui_skeleton` will become the interactive front-end. Integration steps:

- Wire CLI commands to invoke the Python agent conductor (local mode first).  
- Stream normalized IR events back to the CLI for real-time diff views.  
- Expose telemetry hooks so QueryLake can ingest run metadata for fleet-wide analytics.


Appendix: Quick Reference
-------------------------

| Component | Location | Notes |
|-----------|----------|-------|
| Provider router & configs | `agentic_coder_prototype/provider_routing.py` | Defines provider descriptors and schema translators |
| Capability probes | `agentic_coder_prototype/provider_capability_probe.py` | Runs feature tests on startup |
| Route health manager | `agentic_coder_prototype/provider_health.py` | Tracks failures and applies circuit-breaking |
| Normalizer & IR | `agentic_coder_prototype/provider_normalizer.py` | Converts provider results into canonical events |
| Metrics collector | `agentic_coder_prototype/provider_metrics.py` | Aggregates call stats, overrides, fallbacks |
| Regression harness | `tests/test_provider_regression_matrix.py` | Mocked scenarios for guardrails |
| CLI skeleton | `tui_skeleton/` | Node-based prototype for Claude-style terminal UX |
| Runbook | `docs/phase_4/runbook.md` | Daily operations, incident handling |

This document should serve as the zero-context primer for engineers joining the Phase 4 codebase. For deeper historical context, cross-reference `docs/phase_4/HANDOFF_V2.md` and previous phase handoffs.

