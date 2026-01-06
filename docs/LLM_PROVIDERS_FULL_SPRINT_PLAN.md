# KyleCode Multi-Provider Sprint Plan

## Objectives
- Introduce a provider runtime abstraction that decouples KyleCode’s conductor from any single SDK.
- Deliver native integrations for OpenAI (Chat + Responses), OpenRouter (OpenAI-compatible), and Anthropic Messages including tool calling, caching, streaming, and reasoning trace management.
- Extend session state, logging, and telemetry so encrypted chain-of-thought and cache metadata are handled safely.
- Ensure robust fallbacks to text-based dialects and comprehensive test coverage running inside the `ray_sce_test` conda environment.

## Milestones & Tasks

### 1. Runtime Abstraction & State Foundations
- Implement `ProviderRuntime` interface (prep request, execute, stream, normalize tool calls/results, capabilities).
- Build registry keyed off `provider_router` resolution with descriptor metadata (SDK factory, base URL, supports_native_tools, caching knobs, streaming support).
- Refactor conductor to request a runtime instead of instantiating the OpenAI SDK directly; preserve feature-flagged fallback path until tests pass.
- Expand `SessionState` to store provider metadata: conversation IDs, encrypted reasoning blobs, cache usage stats, and pending Anthropic tool calls.
- Introduce `ReasoningTraceStore` abstraction to persist encrypted reasoning for replay while exposing only safe summaries to UI/logs.

### 2. OpenAI & OpenRouter Runtime Support
- Implement OpenAI runtime that selects between Chat Completions and Responses via configuration or model capabilities.
- Generate internally- vs externally-tagged tool schemas automatically; capture `call_id` + `output_item` pairs from Responses.
- Handle encrypted reasoning (`include:["reasoning.encrypted_content"]`), conversation chaining, and GPT-5 control parameters.
- Add streaming multiplexer for Responses events; expose progress callbacks and cancellation plumbing.
- Derive OpenRouter runtime from OpenAI runtime with base URL, attribution headers, SSE keepalive filtering, and routing-aware native tool gating.

### 3. Anthropic Runtime & Tool Flow
- Implement Anthropic Messages runtime with structured content blocks, parallel `tool_use` extraction, and `tool_result` packaging.
- Support `cache_control` breakpoints on tools/system messages and record cache hit/miss metrics.
- Handle thinking blocks carefully: redact from logs, repack when returning tool results, and obey invalidation rules.
- Integrate streaming event parsing (`message_start`, deltas, usage counters) with the shared streaming multiplexer.

### 4. Logging, Telemetry, and Fallbacks
- Update logging v2 to persist provider request/response artifacts with sensitive fields redacted (encrypted reasoning, API keys).
- Record cache usage stats and reasoning summaries in telemetry.
- Ensure provider-native and text-based tool prompts remain coherent; extend provider adapters to normalize responses and craft result envelopes per runtime.
- Maintain fallback to legacy flow when provider runtime not available (model-specific toggles).

### 5. Testing & Validation
- Add unit tests for provider registry resolution, runtime selection, and request normalization.
- Create streaming parser tests for OpenAI Responses, OpenRouter SSE keepalives, and Anthropic events.
- Extend YAML tool tests to assert provider-native schema generation across providers.
- Add integration smoke tests for each runtime behind mocked HTTP fixtures; run full suite inside `ray_sce_test` conda environment.
- Document verification steps and update README/snippets where SDK usage changes.

## Sequencing & Dependencies
1. Complete Milestone 1 before introducing provider-specific runtimes (foundation for all other work).
2. Deliver OpenAI/OpenRouter runtime next; Anthropic runtime builds atop shared abstractions.
3. Logging/telemetry updates rely on runtime outputs; schedule after runtimes are functional.
4. Execute new and existing tests after each milestone using `conda activate ray_sce_test`.

## Risk & Mitigation
- **SDK surface divergence**: wrap raw SDK clients behind runtime interface and mock in tests to avoid tight coupling.
- **Chain-of-thought leakage**: centralize encrypted reasoning handling in `ReasoningTraceStore` and ensure log redaction tests cover regressions.
- **Streaming regressions**: build provider-specific fixtures to validate multiplexer behavior and cancellation handling.
- **Cache policy errors**: record provider usage fields and compare against expected TTL/discount behavior in telemetry tests.

## Deliverables
- Updated conductor, session state, provider runtime modules, adapters, logging, and tests in repository.
- Comprehensive documentation: this plan, inline module docstrings, and README snippets referencing new runtime configuration knobs.
- Passing test suite executed in `ray_sce_test` environment.
