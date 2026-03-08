# Run Request And Context V1

## Purpose

This dossier defines the split between the **host-supplied request** that asks BreadBoard to begin a run and the **engine-resolved context** that the kernel derives before the run loop begins.

The distinction is important for the multi-engine program:

- `RunRequest` is the caller-facing contract.
- `RunContext` is the kernel-facing contract.
- Projections, UI payloads, and host-specific transport envelopes must not collapse the two.

A TypeScript engine must be able to accept the same request semantics and derive an equivalent context without inheriting Python-private objects.

---

## Contract role

### `RunRequest`

`RunRequest` is the minimal durable request submitted by a host, test harness, replay runner, or higher-level SDK. It should answer:

- what the caller wants to run
- against which workspace / profile / config pack
- with which prompt or structured task input
- with which host-visible execution options

`RunRequest` must be serializable and transport-safe.

### `RunContext`

`RunContext` is the resolved execution context that exists after BreadBoard has:

- loaded and merged config
- resolved the provider route
- bound session identity
- decided active mode / feature gates
- resolved workspace roots and policy packs
- determined engine implementation and delegated services

`RunContext` may still be serializable, but it is not authored by the host directly.

---

## Shared semantics that must be frozen

### Request-side invariants

A conforming engine must treat the following as shared request semantics:

- request identity is explicit and stable
- user task / prompt input is preserved exactly as host input
- config selection is by stable ref/path/id, not implementation pointer
- workspace root, if supplied, is host input rather than derived state
- requested execution options are preserved, even if the engine later downgrades them

### Context-side invariants

A conforming engine must derive equivalent context semantics for:

- effective session id
- resolved engine family / runtime mode
- resolved provider route and model id
- active feature flags and loop mode
- resolved workspace root and write policy scope
- policy / permission pack identity
- replay / conformance mode flags

### Derivation rule

The engine must be able to explain how `RunContext` was derived from:

- `RunRequest`
- config pack / dossier / defaults
- environment and provider credentials
- delegated-service availability

The derivation does not need to preserve every internal intermediate step, but the resulting context must be auditably reconstructable.

---

## Non-goals

This contract does not attempt to freeze:

- CLI argument parsing details
- transport-specific request wrappers
- webview or TUI projection state
- Python object lifetimes or dependency injection details
- Ray scheduling metadata
- sandbox container internals

Those may influence context derivation, but they are not themselves `RunRequest` / `RunContext` truth.

---

## Suggested first schema split

### `bb.run_request.v1`

First-pass stable fields should include:

- `schema_version`
- `request_id`
- `entry_mode`
- `task`
- `config_ref`
- `workspace_root`
- `requested_model`
- `requested_features`
- `requested_execution`
- `metadata`

### `bb.run_context.v1`

First-pass stable fields should include:

- `schema_version`
- `session_id`
- `engine_family`
- `engine_ref`
- `request_id`
- `workspace_root`
- `resolved_model`
- `resolved_provider_route`
- `active_mode`
- `feature_flags`
- `execution_mode`
- `delegated_services`

---

## Current Python owners

The relevant current Python ownership is split across:

- `agentic_coder_prototype/conductor_bootstrap.py`
- `agentic_coder_prototype/runtime_context.py`
- `agentic_coder_prototype/conductor_context.py`
- `agentic_coder_prototype/agent_llm_openai.py`
- `agentic_coder_prototype/state/session_state.py`
- replay and CLI/session runner entrypoints

This ownership is still more implicit than ideal. The contract program should make the resulting `RunContext` explicit even if the Python implementation continues to assemble it in stages.

---

## Current evidence / tests

Current evidence is indirect rather than explicitly contract-shaped:

- CLI bridge request models and exports
- session runner request handling tests
- replay task execution tests
- provider-routing and mode-selection tests
- many end-to-end conformance lanes

The important gap is that request/context equivalence is currently *implied* by behavior rather than frozen as its own contract family.

---

## Known ambiguities

Still unresolved at this stage:

- whether `RunContext` should carry provider credentials by ref only or by already-resolved secret handle
- whether delegated service selection belongs in `RunContext` or in a sibling engine-private structure
- how much replay strictness should be visible at `RunRequest` time versus context derivation time
- whether host-specified task import / resumed-session references belong in `RunRequest` or an adjacent import contract

---

## Why this contract matters for TypeScript

A TS engine can only be credible if it can accept the same run request semantics and derive the same effective context semantics as the Python reference engine.

If this boundary remains implicit, the TS effort will drift immediately into host-specific convenience APIs rather than shared kernel behavior.

---

## Immediate next steps

1. add `bb.run_request.v1.schema.json`
2. add `bb.run_context.v1.schema.json`
3. add one minimal example for each
4. add the first engine-fixture row proving a Python reference request/context pair
