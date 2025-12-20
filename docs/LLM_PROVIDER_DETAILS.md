## LLM Provider Details and Integration Guide for BreadBoard

This document consolidates provider behaviors and SDK patterns for OpenRouter, OpenAI, and Anthropic as they relate to BreadBoard’s modular agentic coder. It focuses on native tool calling, input/KV caching, chain-of-thought handling, and streaming. It also maps these capabilities to BreadBoard’s architecture (`provider_routing`, `provider_adapters`, `dialects`, and execution policies).

### Table of Contents

- [1. Overview](#1-overview)
- [2. BreadBoard Architecture Hooks](#2-breadboard-architecture-hooks)
  - [2.1 Where provider behavior plugs in](#21-where-provider-behavior-plugs-in)
  - [2.2 Text vs provider-native tools](#22-text-vs-provider-native-tools)
- [3. OpenRouter](#3-openrouter)
  - [3.1 Endpoints and SDK setup](#31-endpoints-and-sdk-setup)
  - [3.2 Request/response shape](#32-requestresponse-shape)
  - [3.3 Tool calling](#33-tool-calling)
  - [3.4 Prompt/input caching](#34-promptinput-caching)
  - [3.5 Streaming](#35-streaming)
  - [3.6 Limits and error handling](#36-limits-and-error-handling)
- [4. OpenAI](#4-openai)
  - [4.1 Chat Completions vs Responses vs Conversations](#41-chat-completions-vs-responses-vs-conversations)
  - [4.2 Function/tool calling across APIs](#42-functiontool-calling-across-apis)
  - [4.3 Prompt caching and statefulness](#43-prompt-caching-and-statefulness)
  - [4.4 Chain-of-thought and encrypted reasoning items](#44-chain-of-thought-and-encrypted-reasoning-items)
  - [4.4.1 Reasoning summaries (safe-to-display)](#441-reasoning-summaries-safe-to-display)
  - [4.5 Streaming](#45-streaming)
  - [4.6 GPT-5 parameter changes](#46-gpt-5-parameter-changes)
- [5. Anthropic (Claude)](#5-anthropic-claude)
  - [5.1 Messages API](#51-messages-api)
  - [5.2 Tool use: client tools and server tools](#52-tool-use-client-tools-and-server-tools)
  - [5.3 Prompt caching (`cache_control`) and TTL](#53-prompt-caching-cache_control-and-ttl)
  - [5.4 Extended thinking and chain-of-thought handling](#54-extended-thinking-and-chain-of-thought-handling)
  - [5.5 Streaming and usage reporting](#55-streaming-and-usage-reporting)
- [6. Cross-Provider Comparison Tables](#6-cross-provider-comparison-tables)
  - [6.1 Message and item shapes](#61-message-and-item-shapes)
  - [6.2 Tool schema mapping](#62-tool-schema-mapping)
  - [6.3 Streaming behavior](#63-streaming-behavior)
  - [6.4 Caching capabilities](#64-caching-capabilities)
  - [6.5 Chain-of-thought support](#65-chain-of-thought-support)
- [7. SDK Samples and Validation Snippets](#7-sdk-samples-and-validation-snippets)
  - [7.1 OpenRouter (OpenAI SDK compatible)](#71-openrouter-openai-sdk-compatible)
  - [7.2 OpenAI Responses vs Chat Completions](#72-openai-responses-vs-chat-completions)
  - [7.3 Anthropic Messages + Tools](#73-anthropic-messages--tools)
- [8. BreadBoard Integration Guidance](#8-breadboard-integration-guidance)
  - [8.1 Provider router and model IDs](#81-provider-router-and-model-ids)
  - [8.2 Provider adapters (native tools, shapes, streaming)](#82-provider-adapters-native-tools-shapes-streaming)
  - [8.3 Dialect selection, text-based tools, and fallback](#83-dialect-selection-text-based-tools-and-fallback)
  - [8.4 Caching policy integration](#84-caching-policy-integration)
  - [8.5 Chain-of-thought handling in BreadBoard](#85-chain-of-thought-handling-in-breadboard)
  - [8.6 Streaming plumbing and cancellation](#86-streaming-plumbing-and-cancellation)
- [9. Edge Cases and Gotchas](#9-edge-cases-and-gotchas)
- [10. Test Plans and Checklists](#10-test-plans-and-checklists)

---

## 1. Overview

BreadBoard needs to support heterogeneous provider APIs while keeping a unified developer experience. We target:

- OpenRouter: OpenAI-compatible SDK interface with chat completions. Adds routing, caching abstraction, SSE streaming details, and provider-specific capabilities.
- OpenAI: Moving from Chat Completions to the Responses API for stateful, agentic primitives; function/tool calling differences; encrypted reasoning items; Conversations for state.
- Anthropic: Messages API with explicit `tools`, structured `tool_use` and `tool_result` blocks, prompt caching via `cache_control` with 5m/1h TTL, and explicit guidance for extended thinking.

We standardize around four themes:

1) Native tool calling (provider-supplied schemas and server tools) alongside our text-based tool dialects.
2) Input/KV caching: automatic (OpenAI/OpenRouter) vs explicit `cache_control` (Anthropic), pricing multipliers, and TTL.
3) Chain-of-thought: encrypted reasoning pass-through (OpenAI Responses), thinking blocks (Anthropic), and safe passback.
4) Streaming: SSE chunking, event types, cancellation, and error semantics.

## 2. BreadBoard Architecture Hooks

### 2.1 Where provider behavior plugs in

- `agentic_coder_prototype/provider_routing.py`: Parses model IDs (e.g., `openrouter/openai/gpt-5-nano`) and returns provider config, base URL, headers, and whether native tools are supported.
- `agentic_coder_prototype/provider_adapters.py`: Translates our internal tool definitions into provider-native schema and normalizes provider responses (OpenAI-like functions, Anthropic `tool_use`).
- `agentic_coder_prototype/execution/dialect_manager.py` + `execution/composite.py`: Text-based tools (pythonic, bash blocks, unified diffs) when native tools are disabled or not supported.
- `agentic_coder_prototype/execution/enhanced_executor.py`: Policy application (permissions, path normalization, edit-before-bash), LSP feedback, and workspace state tracking.

### 2.2 Text vs provider-native tools

- Prefer native tools when provider support is strong and reliable (OpenAI Responses, Anthropic messages tools). Toggle via config (`provider_tools.use_native`) and per-tool YAML (`provider_routing.openai.native_primary: true`).
- Keep text-based dialects as fallback and for formats like diffs that are more reliable under strict policies (Aider/OpenCode/Unified diffs).

## 3. OpenRouter

OpenRouter exposes a unified API, generally compatible with the OpenAI SDK, and offers a chat completions endpoint with SSE streaming. It also provides routing, prompt caching abstraction, and detailed streaming cancellation semantics.

### 3.1 Endpoints and SDK setup

- Base URL: `https://openrouter.ai/api/v1`
- Common endpoint for chat: `POST /chat/completions`
- Optional attribution headers: `HTTP-Referer`, `X-Title`

TypeScript (OpenAI SDK):

```ts
import OpenAI from "openai";

const client = new OpenAI({
  apiKey: process.env.OPENROUTER_API_KEY,
  baseURL: "https://openrouter.ai/api/v1",
});

const completion = await client.chat.completions.create({
  model: "openai/gpt-4o",
  messages: [{ role: "user", content: "What is the meaning of life?" }],
  extra_headers: {
    "HTTP-Referer": "https://your.app/",
    "X-Title": "Your App",
  },
});

console.log(completion.choices[0].message.content);
```

Python (requests, direct API):

```python
import requests, json

r = requests.post(
    "https://openrouter.ai/api/v1/chat/completions",
    headers={
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # Optional attribution
        "HTTP-Referer": "https://your.app/",
        "X-Title": "Your App",
    },
    data=json.dumps({
        "model": "openai/gpt-4o",
        "messages": [{"role": "user", "content": "Hello!"}],
    }),
)
print(r.json())
```

### 3.2 Request/response shape

- Request follows OpenAI Chat Completions structure (roles: `system`, `user`, `assistant`).
- Responses include `choices[0].delta` when streaming, and `choices[0].message` when not streaming.

### 3.3 Tool calling

- OpenRouter supports tool calling to the extent supported by the underlying provider. For OpenAI-family models, pass standard OpenAI tool/function specs. For others, behavior varies by provider.
- Recommendation for BreadBoard: Gate native tools to known-good models/providers via `provider_routing` and per-tool YAML preferences; otherwise use text-based tool dialects.

### 3.4 Prompt/input caching

- OpenRouter exposes provider prompt-caching in a unified way.
  - OpenAI: automatic prompt caching; minimum ~1024 tokens for cache writes; cache reads at discounted rate.
  - Anthropic: requires explicit `cache_control` blocks; 5-minute default TTL, optional 1-hour TTL; writes more expensive, reads cheap.
  - Gemini (implicit caching on some models): discounted cache reads; minimum tokens for cache eligibility.
- Inspect cache usage via activity page, generation API, or `usage: { include: true }` in requests. Responses may include `cache_discount` and cache token counts.

### 3.5 Streaming

- Enable with `"stream": true`. Server-Sent Events (SSE) stream lines starting with `data: ...`; terminates with `data: [DONE]`.
- OpenRouter occasionally sends comment keepalives like `: OPENROUTER PROCESSING` which should be ignored per SSE spec.
- Cancellation: Abort the HTTP connection; supported for many providers (OpenAI, Anthropic, etc.), not all (e.g., Bedrock, Google as of their list). Mid-stream errors are emitted as SSE `data:` payloads with an `error` field and `finish_reason: "error"`.

Python (streaming via requests):

```python
import requests, json

resp = requests.post(
    "https://openrouter.ai/api/v1/chat/completions",
    headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
    json={
        "model": MODEL,
        "messages": [{"role": "user", "content": "Write a story"}],
        "stream": True,
    },
    stream=True,
)

for line in resp.iter_lines():
    if not line:
        continue
    text = line.decode("utf-8")
    if not text.startswith("data: "):
        continue
    data = text[6:]
    if data == "[DONE]":
        break
    payload = json.loads(data)
    delta = payload["choices"][0]["delta"].get("content")
    if delta:
        print(delta, end="", flush=True)
```

### 3.6 Limits and error handling

- Pre-stream errors return standard JSON error with HTTP code (400/401/402/429/5xx).
- Mid-stream errors are emitted as SSE `data:` payloads with top-level `error` and terminated stream.

## 4. OpenAI

OpenAI is migrating from Chat Completions to the Responses API, with Conversations for durable state. The Responses API unifies items (messages, function calls, tool outputs), offers better native tool integration, caching, and encrypted reasoning transfer.

### 4.1 Chat Completions vs Responses vs Conversations

- Chat Completions: `POST /v1/chat/completions`, array of `messages` with roles, optional `functions/tools`. Returns `choices[].message` or streamed `delta` chunks.
- Responses: `POST /v1/responses`, accepts `input` (string or array of role/content items) and optional `instructions`. Returns a single `response` with `output` array of typed items (e.g., `reasoning`, `message`, `function_call`, `function_call_output`). Stored by default unless `store: false`.
- Conversations: a durable conversation object ID you can pass to Responses to maintain state across sessions. Alternatively, chain responses via `previous_response_id`.

Examples:

```ts
// Chat Completions (TS)
import OpenAI from "openai";
const client = new OpenAI();
const completion = await client.chat.completions.create({
  model: "gpt-5",
  messages: [
    { role: "system", content: "You are a helpful assistant." },
    { role: "user", content: "Hello!" },
  ],
});
console.log(completion.choices[0].message.content);
```

```ts
// Responses (TS)
import OpenAI from "openai";
const client = new OpenAI();
const response = await client.responses.create({
  model: "gpt-5",
  instructions: "You are a helpful assistant.",
  input: "Hello!",
});
console.log(response.output_text);
```

```ts
// Responses with Conversations (TS)
const conversation = "conv_..."; // created via Conversations API
const response = await client.responses.create({
  model: "gpt-4o-mini",
  input: [{ role: "user", content: "What are the 5 Ds of dodgeball?" }],
  conversation,
});
```

```ts
// Chaining with previous_response_id (TS)
const res1 = await client.responses.create({ model: "gpt-4o-mini", input: "tell me a joke", store: true });
const res2 = await client.responses.create({
  model: "gpt-4o-mini",
  previous_response_id: res1.id,
  input: [{ role: "user", content: "explain why this is funny." }],
  store: true,
});
```

Key differences:

- Responses is a superset: one generation per call, emits semantic events, and better integrates tools and reasoning.
- Storage: Responses objects stored by default (disable with `store: false`). Conversations persist beyond the 30-day TTL for standalone Responses.

### 4.2 Function/tool calling across APIs

- Chat Completions: `functions`/`tools` on request; function calls returned inside `choices[].message.tool_calls` or similar, arguments as JSON.
- Responses: `tools` defined with internally tagged function objects ({"type":"function","name","parameters":{...}}). Tool calls and their outputs are separate items correlated with a `call_id`.

Chat Completions function definition (externally tagged):

```json
{
  "type": "function",
  "function": {
    "name": "get_weather",
    "description": "Determine weather in my location",
    "strict": true,
    "parameters": {
      "type": "object",
      "properties": { "location": { "type": "string" } },
      "additionalProperties": false,
      "required": ["location", "unit"]
    }
  }
}
```

Responses function definition (internally tagged, strict by default):

```json
{
  "type": "function",
  "name": "get_weather",
  "description": "Determine weather in my location",
  "parameters": {
    "type": "object",
    "properties": { "location": { "type": "string" } },
    "additionalProperties": false,
    "required": ["location", "unit"]
  }
}
```

Recommendations for BreadBoard:

- Provider adapter should generate both shapes depending on API selection.
- Treat Responses tool calls and tool outputs as separate events; map to `EnhancedToolExecutor` with `call_id` correlation.

### 4.3 Prompt caching and statefulness

- OpenAI prompt caching is automatic on supported models, with minimum prompt size (~1024 tokens). Cache reads are charged at discounted rates per model; writes typically no extra cost.
- To keep state without manually threading messages, use Conversations or `previous_response_id`.
- Data retention: Responses stored by default unless `store: false`. Conversation items avoid the 30-day TTL.

### 4.4 Chain-of-thought and encrypted reasoning items

- Responses API can include a `reasoning` item. For ZDR or stateless workflows, set `store: false` and add `include: ["reasoning.encrypted_content"]`. The API will return encrypted reasoning that you can pass back in subsequent requests to preserve context without revealing chain-of-thought.
- BreadBoard should store and forward encrypted reasoning items when present, but never display or log decrypted content.

References: see the OpenAI Reasoning guide [Reasoning](https://platform.openai.com/docs/guides/reasoning) for authoritative details on reasoning items, summaries, and encrypted content in the Responses API.

Additional notes:

- Stateful usage: when using Conversations (or `store: true`), OpenAI persists conversation state internally; you typically do not need to handle encrypted reasoning content yourself. The raw chain-of-thought is not exposed; the provider retains necessary state.
- Stateless/ZDR usage: set `store: false` and request `include: ["reasoning.encrypted_content"]`. The service returns an encrypted payload that you must pass back verbatim in future calls to maintain reasoning context. Do not attempt to inspect or transform it.
- Summaries vs chain-of-thought: the `reasoning` item may contain a high-level `summary` suitable for user display. Treat this as descriptive only; do not feed summaries back to the model as a substitute for encrypted reasoning items.

Example (TS):

```ts
const res = await client.responses.create({
  model: "gpt-5",
  input: "Deep question...",
  store: false,
  include: ["reasoning.encrypted_content"],
});
// Persist res.output items securely; pass back encrypted reasoning in next call
```

#### 4.4.1 Reasoning summaries (safe-to-display)

- In the Responses API, the model may include a `reasoning` item whose structure can contain a human-readable summary (e.g., `summary`) separate from the full chain-of-thought.
- The summary is intended for safe display to users (e.g., progress updates, “what I’m thinking” previews) and documentation/specs. Do not treat summary text as authoritative state for future requests.
- For preserving internal model context, pass back only the encrypted reasoning item when applicable, not the summary.

Extracting reasoning summaries (TypeScript):

```ts
import OpenAI from "openai";

const client = new OpenAI();
const res = await client.responses.create({
  model: "gpt-5",
  input: "Plan work on task X",
  // Optional: include encrypted reasoning for stateless workflows
  store: false,
  include: ["reasoning.encrypted_content"],
});

// Collect safe-to-display summary text (if present)
const reasoningSummaries = res.output
  .filter((item: any) => item.type === "reasoning")
  .flatMap((item: any) => item.summary ?? [])
  .map((part: any) => (part.type === "output_text" ? part.text : ""))
  .join("");

// UI: stream/append reasoningSummaries to a progress panel; do not send it back to the API
```

Extracting reasoning summaries (Python):

```python
from openai import OpenAI

client = OpenAI()
res = client.responses.create(
    model="gpt-5",
    input="Plan work on task X",
    store=False,
    include=["reasoning.encrypted_content"],
)

summary_text_parts = []
for item in res.output:
    if getattr(item, "type", None) == "reasoning":
        for part in getattr(item, "summary", []) or []:
            if getattr(part, "type", None) == "output_text":
                summary_text_parts.append(getattr(part, "text", ""))

reasoning_summary = "".join(summary_text_parts)
# UI: display reasoning_summary in a non-persistent progress view; avoid feeding it back into prompts
```

BreadBoard guidance:

- Treat `reasoning.summary` as a UI affordance for progress/status. Show in transcripts or live stream panes with a clear label (e.g., “Model’s high-level plan”).
- Do not pass summaries back as input. For continuity across turns, rely on Conversations or `previous_response_id`, and on encrypted reasoning items when using stateless mode.
- Redaction: summaries are generally safe to display, but still apply standard redaction policy if user/content rules require it. Never attempt to decrypt or infer raw chain-of-thought from summaries.

### 4.5 Streaming

- Chat Completions: SSE `data:` lines, `choices[].delta` with `content`; `[DONE]` terminator.
- Responses: Event-driven streaming with typed item events; SDKs provide `output_text` helper to reconstruct text.

### 4.6 GPT-5 parameter changes

- Some classic parameters are not supported for GPT-5 family (e.g., `temperature`, `top_p`, `logprobs`).
- Use GPT-5 specific controls:
  - `reasoning: { effort: "minimal" | "low" | "medium" | "high" }`
  - `text: { verbosity: "low" | "medium" | "high" }`
  - `max_output_tokens`

Example (JSON):

```json
{
  "model": "gpt-5-mini",
  "input": [{"role":"user","content":"Summarize the protocol."}],
  "reasoning": {"effort":"high"},
  "text": {"verbosity":"medium"},
  "max_output_tokens": 800
}
```

## 5. Anthropic (Claude)

Anthropic’s Messages API supports tool use with explicit `tool_use` and `tool_result` blocks, parallel tool calls, and prompt caching with `cache_control` breakpoints (5m default TTL, optional 1h). They provide detailed guidance for extended thinking and how thinking blocks interact with caching.

### 5.1 Messages API

- Endpoint: `POST /v1/messages`
- Required headers: `x-api-key`, `anthropic-version`, `content-type`
- Roles: messages include `role` (`user` or `assistant`), and `content` blocks (text, images, tool_use, tool_result).

### 5.2 Tool use: client tools and server tools

- Client tools: defined in request `tools` with `name`, `description`, `input_schema` (JSON Schema). Model returns `tool_use` block with `id`, `name`, and validated `input` when it decides to call a tool. Client executes and returns `tool_result` with matching `tool_use_id`.
- Server tools: certain tools (e.g., web search, web fetch) that run on Anthropic’s servers when enabled. Results are automatically incorporated; no client execution required.
- Parallel tool use: Multiple `tool_use` blocks in a single assistant message; client must respond with a single user message containing all corresponding `tool_result` blocks.

Example (single tool):

```bash
curl https://api.anthropic.com/v1/messages \
  -H "content-type: application/json" \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-opus-4-1-20250805",
    "max_tokens": 1024,
    "tools": [{
      "name": "get_weather",
      "description": "Get the current weather in a given location",
      "input_schema": {
        "type": "object",
        "properties": {
          "location": {"type": "string", "description": "City, e.g. San Francisco, CA"}
        },
        "required": ["location"]
      }
    }],
    "messages": [{"role": "user", "content": "Weather in SF?"}]
  }'
```

Assistant response contains `tool_use` block with `id` and arguments; client echoes a `tool_result` with `tool_use_id`.

### 5.3 Prompt caching (`cache_control`) and TTL

- `cache_control: { type: "ephemeral", ttl: "5m" | "1h" }` can be attached to content blocks in `tools`, `system`, or `messages` to mark cache breakpoints.
- Minimum cacheable length typically 1024 tokens for many models (some Haiku variants 2048). Up to 4 breakpoints allowed. Automatic prefix checking uses the longest matching prefix (looks back ~20 content blocks per breakpoint).
- Pricing multipliers (illustrative): writes (5m ~1.25x, 1h ~2x base input), reads (~0.1x base input).
- Cache invalidation follows hierarchy `tools → system → messages`. Changes higher up invalidate lower caches. Images and `tool_choice` changes can invalidate caches.

Examples:

```json
{
  "messages": [
    {
      "role": "system",
      "content": [
        {"type": "text", "text": "You are a historian..."},
        {"type": "text", "text": "HUGE TEXT BODY", "cache_control": {"type": "ephemeral"}}
      ]
    },
    {"role": "user", "content": [{"type":"text", "text":"What triggered the collapse?"}]}
  ]
}
``;

```json
{
  "tools": [
    {"name": "get_time", "description": "...", "input_schema": {"type": "object","properties": {"timezone": {"type": "string"}}, "required": ["timezone"]},
     "cache_control": {"type": "ephemeral"}}
  ]
}
```

### 5.4 Extended thinking and chain-of-thought handling

- Thinking blocks (extended reasoning) can appear in assistant content. They cannot be directly marked with `cache_control` but can be cached implicitly when included in previous assistant turns returned back to the API with tool results.
- When non-tool-result user content is added later, earlier thinking blocks may be stripped from context per Anthropic rules.
- For BreadBoard: Treat thinking blocks as sensitive; store minimally, do not expose in user-facing logs; follow Anthropic caching behavior when constructing subsequent requests.

### 5.5 Streaming and usage reporting

- Anthropic supports streaming with granular events (e.g., `message_start`, content block deltas). Usage includes `cache_creation_input_tokens`, `cache_read_input_tokens`, `input_tokens`, and `output_tokens`. When streaming, `message_start` may carry usage counters early.

## 6. Cross-Provider Comparison Tables

### 6.1 Message and item shapes

- OpenRouter: OpenAI Chat Completions-compatible (roles + messages array).
- OpenAI Chat: roles/messages; OpenAI Responses: single `response` with `output[]` typed items.
- Anthropic: Messages with `content` blocks (text, images, tool_use, tool_result).

### 6.2 Tool schema mapping

- OpenAI Chat: `functions`/`tools` (externally tagged function object). Arguments JSON.
- OpenAI Responses: `tools` with internally tagged function objects; strict by default; tool call and output are separate items correlated by `call_id`.
- Anthropic: `tools` array with `input_schema` (JSON Schema). Assistant emits `tool_use` content blocks; client returns `tool_result` with `tool_use_id`.

### 6.3 Streaming behavior

- OpenRouter: SSE `data:` lines, occasional comments `: OPENROUTER PROCESSING`; `[DONE]` terminator; mid-stream errors arrive as SSE payloads with `error` and `finish_reason: "error"`.
- OpenAI Chat: SSE deltas with `choices[].delta` and `[DONE]`.
- OpenAI Responses: typed streaming events; SDK `output_text` helper to assemble text.
- Anthropic: message-level streaming with content block deltas; usage reported in early events.

### 6.4 Caching capabilities

- OpenRouter: abstracts provider caching; OpenAI auto; Anthropic via `cache_control`; Gemini implicit on select models.
- OpenAI: automatic prompt caching on eligible models (min size), discounted reads; Conversations for durable state.
- Anthropic: `cache_control` breakpoints; 5m default with optional 1h; pricing multipliers for writes/reads; hierarchy invalidation.

### 6.5 Chain-of-thought support

- OpenAI: encrypted reasoning items with `include: ["reasoning.encrypted_content"]` when `store:false`. Pass back verbatim to preserve reasoning context.
- Anthropic: thinking blocks returned openly (subject to product settings); caching interacts with tool results and non-tool user content as described.
- OpenRouter: passes through based on underlying provider.

## 7. SDK Samples and Validation Snippets

### 7.1 OpenRouter (OpenAI SDK compatible)

```ts
import OpenAI from "openai";

const client = new OpenAI({ apiKey: process.env.OPENROUTER_API_KEY, baseURL: "https://openrouter.ai/api/v1" });

const stream = await client.chat.completions.create({
  model: "openai/gpt-4o",
  stream: true,
  messages: [{ role: "user", content: "Write a haiku about rain." }],
});
```

### 7.2 OpenAI Responses vs Chat Completions

Function/tool definition shapes:

```json
// Chat Completions function (externally tagged)
{
  "type": "function",
  "function": {
    "name": "get_weather",
    "parameters": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}
  }
}
```

```json
// Responses function (internally tagged)
{
  "type": "function",
  "name": "get_weather",
  "parameters": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}
}
```

Streaming (Chat): SSE `choices[].delta.content`; Streaming (Responses): typed events; use SDK helpers to reconstruct output text.

### 7.3 Anthropic Messages + Tools

Tool definition with JSON Schema and later `tool_use` and `tool_result` correlation via IDs:

```json
{
  "tools": [
    {
      "name": "get_weather",
      "description": "Get the current weather in a given location",
      "input_schema": {
        "type": "object",
        "properties": {
          "location": {"type": "string"},
          "unit": {"type":"string","enum":["celsius","fahrenheit"]}
        },
        "required": ["location"]
      }
    }
  ]
}
```

Client returns:

```json
{
  "role": "user",
  "content": [
    {"type": "tool_result", "tool_use_id": "toolu_xxx", "content": "15 degrees"}
  ]
}
```

## 8. BreadBoard Integration Guidance

### 8.1 Provider router and model IDs

- Use model IDs like `openrouter/openai/gpt-5-nano` to route through OpenRouter with OpenAI-compatible schema.
- Resolve provider configs for base URL, headers, and whether native tools are supported.

### 8.2 Provider adapters (native tools, shapes, streaming)

- OpenAI adapter:
  - Translate internal tool defs to Chat or Responses schema (respect GPT-5 control fields).
  - Extract tool calls from provider messages; correlate with `call_id` (Responses) or tool call IDs (Chat).
  - Handle encrypted reasoning items by preserving `reasoning.encrypted_content`.
- Anthropic adapter:
  - Translate internal tool defs to `tools[]` with `input_schema`.
  - Parse `tool_use` blocks and repackage results into `tool_result`.
  - Respect `cache_control` for breakpoints when configured.

### 8.3 Dialect selection, text-based tools, and fallback

- Favor native tools where configured and reliable; otherwise use text-based dialects (pythonic, bash, diffs).
- Enforce “one bash per turn” and reorder edits before bash.
- Suppress per-turn tool prompts when using provider-native tools if configured (to avoid mixed instructions degrading native tool usage).

### 8.4 Caching policy integration

- OpenAI/OpenRouter: rely on automatic caching; keep stable, large system/tool content consistent across turns to maximize hits. Consider Conversations for long threads.
- Anthropic: emit `cache_control` on stable `tools`/`system` and final user blocks for incremental caching. Support 5m by default; optionally allow 1h for longer gaps. Track usage fields to monitor savings.

### 8.5 Chain-of-thought handling in BreadBoard

- OpenAI: if `include: ["reasoning.encrypted_content"]` is present, persist and pass back verbatim on next turn; do not attempt to decrypt; redact from logs.
- Anthropic: treat thinking blocks as sensitive; do not surface in user-facing logs; follow cache-invalidation rules when non-tool user content is added.

### 8.6 Streaming plumbing and cancellation

- Implement SSE readers tolerant of keepalive comments and mid-stream error payloads.
- Support cancellation by aborting HTTP requests where providers support cancellation. Fall back gracefully when providers do not support cancellation (documented by OpenRouter).

## 9. Edge Cases and Gotchas

- GPT-5 controls: avoid unsupported parameters (`temperature`, `top_p`, `logprobs`) and use `reasoning.effort`, `text.verbosity`, and `max_output_tokens`.
- Mixed modes: When native tools are enabled, suppress text-based tool prompts to avoid confusing the provider.
- Anthropic parallel tools: Client must provide all `tool_result` blocks in a single user message; otherwise tool flow stalls.
- Anthropic cache: Only text portions in multipart can carry `cache_control`; minimum token thresholds apply; hierarchy invalidation can wipe caches unexpectedly if you alter tools or system content.
- OpenRouter streaming: handle comment lines and mid-stream error envelopes; do not assume all providers support cancellation.

## 10. Test Plans and Checklists

- Tool calling
  - OpenAI Chat: define function; ensure tool call emitted; execute and return result; verify assistant incorporates result.
  - OpenAI Responses: define tool; verify separate tool call and tool output items; ensure `call_id` correlation handled.
  - Anthropic: define `tools` with `input_schema`; verify `tool_use` and `tool_result` roundtrip; test parallel tool usage.

- Caching
  - OpenAI/OpenRouter: send identical system/tool context over multiple calls; confirm reduced input cost/latency where reported.
  - Anthropic: add `cache_control` to `tools`/`system` and final user block; verify `cache_read_input_tokens` increases on subsequent calls; test 5m vs 1h TTL.

- Chain-of-thought
  - OpenAI: set `store:false` and `include:["reasoning.encrypted_content"]`; verify encrypted reasoning item is returned and accepted in subsequent call.
  - Anthropic: simulate thinking blocks + tool flows; verify caching behavior and stripping when non-tool user content is added.

- Streaming and cancellation
  - OpenRouter: test SSE reader robustness to comments and mid-stream errors; test cancellation where supported.
  - OpenAI Responses: test typed streaming events via SDK; ensure transcript assembly matches `output_text`.
  - Anthropic: test message streaming and usage counters; ensure client handles partial deltas.

---

This guide should be used alongside BreadBoard’s `FULL_EXPLANATION_SPEC.md` to implement and validate robust provider support with minimal surprises across tool calling, caching, chain-of-thought handling, and streaming.


