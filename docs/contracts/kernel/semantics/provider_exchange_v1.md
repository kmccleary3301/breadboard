# Provider Exchange V1

## Purpose

This dossier defines the shared kernel contract for **provider exchange**.

Provider exchange is the normalized boundary between BreadBoard and an underlying model provider. It is not a raw HTTP trace, and it is not a host/UI projection. It is the kernel-level representation of:

- the request the engine asked a provider runtime to execute
- the normalized response/result the runtime returned
- the metadata necessary to audit replay, fallback, and conformance

A TypeScript engine should be able to produce the same provider-exchange shape even if the underlying SDK differs from Python.

---

## Contract role

Provider exchange sits between:

- request/context resolution
- provider runtime invocation
- model-output normalization
- replay / conformance evidence

It should expose the **semantic provider interaction** without overfitting transport details.

---

## Shared semantics that must be frozen

A conforming engine must preserve the following provider-exchange semantics:

- resolved provider family / runtime id
- resolved model id
- effective streaming posture
- normalized outbound message/tool schema surface
- provider-visible prompt/tool content after final synthesis
- normalized inbound messages
- normalized tool calls and finish reason
- usage/accounting metadata where available
- audit metadata for fallback / override / probe decisions

The contract should preserve enough information to compare engines at the provider-boundary level without requiring raw transport parity.

---

## Boundary rule

There are three distinct layers here:

1. **provider request boundary**
   - what the kernel asked the provider runtime to do
2. **provider response boundary**
   - what the provider runtime returned in normalized form
3. **raw evidence**
   - raw HTTP bodies, SSE fragments, SDK responses, logged dumps

Only the first two are kernel contract candidates.

Raw evidence must be linked by reference, not elevated into the kernel contract itself.

---

## Non-goals

This contract does not freeze:

- exact HTTP headers
- exact SSE frame segmentation
- SDK-specific object models
- internal retry implementation details
- Python `openai` or `anthropic` client types
- Vercel AI SDK object shapes

Those belong to runtime adapters and evidence bundles, not the contract itself.

---

## Recommended first schema content

### Request-side shape

First-pass `bb.provider_exchange.v1` should capture:

- `exchange_id`
- `session_id`
- `turn_index`
- `provider_family`
- `runtime_id`
- `route_id`
- `model`
- `stream`
- `messages`
- `tools`
- `metadata`

### Response-side shape

The same contract should also capture:

- normalized `messages`
- `usage`
- `finish_reason` or equivalent finish metadata
- fallback / retry annotations
- health / capability annotations
- optional references to raw evidence

---

## Current Python owners

Current ownership is spread across:

- `agentic_coder_prototype/provider_ir.py`
- `agentic_coder_prototype/provider_normalizer.py`
- `agentic_coder_prototype/provider_runtime.py`
- `agentic_coder_prototype/provider_runtime_replay.py`
- `agentic_coder_prototype/provider_invoker.py`
- invocation sites in `agentic_coder_prototype/agent_llm_openai.py` and `conductor_execution.py`

The explicit contract boundary is still weaker than ideal. The immediate Python cleanup should make the request/response exchange record explicit even if transport behavior remains unchanged.

---

## Current evidence / tests

Current evidence already exists for parts of this family:

- `tests/test_provider_ir.py`
- `tests/test_provider_normalizer.py`
- `tests/test_provider_invoker.py`
- `tests/providers/test_provider_runtime_*`
- provider replay tests
- target-harness conformance evidence for strict replay lanes

These tests are strong, but they still validate fragments rather than one shared provider-exchange contract.

---

## Known ambiguities

Still unresolved:

- how much reasoning metadata is kernel-shared versus provider-private
- how to represent provider-plan auth/limits metadata without dragging transport details into the contract
- whether streaming deltas should be part of provider exchange or the kernel event family only
- whether capability-probe decisions belong directly in the exchange or as side metadata

---

## Why this matters for TypeScript

The TS engine will likely use different provider client libraries. Without a frozen provider-exchange contract, each engine will normalize provider behavior differently and replay/conformance will lose rigor quickly.

---

## Immediate next steps

1. add `bb.provider_exchange.v1.schema.json`
2. add a minimal example request/response exchange
3. add a Python helper that makes request and response exchange records explicit
4. add the first conformance fixture family for provider exchange
