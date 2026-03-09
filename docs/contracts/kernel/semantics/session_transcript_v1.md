# Session Transcript V1

## Purpose

`session_transcript_v1` is the normalized derived-state representation of what a run/session contains from the perspective of conversational and model-visible state.

This contract is not the canonical runtime truth surface. The canonical truth surface is the kernel event log. The transcript is a stable, derived representation.

---

## Contract Role

The transcript contract should answer:

- what messages and model-visible artifacts existed
- in what stable order they existed
- what tool calls and tool results were model-visible
- what provenance and lineage are required for replay and reconstruction
- what annotations are host-only versus model-visible

---

## Current Python Owners

Primary current owners and related surfaces:

- `agentic_coder_prototype/agent_session.py`
- `agentic_coder_prototype/turn_context.py`
- `agentic_coder_prototype/logging_v2/markdown_transcript.py`
- `agentic_coder_prototype/reasoning_trace_store.py`
- `agentic_coder_prototype/todo/store.py`
- longrun/checkpoint paths that snapshot session state

---

## Shared Semantics That Must Be Frozen

### 1. Transcript is derived

The transcript must be derived from kernel events or equivalent normalized runtime truth.

It must not be treated as a freeform UI log.

### 2. Stable ordering and lineage

The transcript must preserve:

- message ordering
- tool call ordering
- tool result ordering
- stable call IDs
- provenance back to relevant event lineage where necessary

### 3. Model-visible versus host-only annotations

The transcript must be able to distinguish:

- content shown to the model
- content shown only to the user/operator/host
- metadata used for replay or diagnostics but not visible in the conversation

### 4. Reasoning metadata discipline

Reasoning traces and summaries must not silently blur into normal assistant content.

The transcript contract should make clear whether reasoning is:

- model-visible
- host-visible only
- summarized
- provider-native but hidden from the host UI

### 5. Tool record integration

Tool calls and results need stable representation in transcript space, but only their model-visible form belongs directly in transcript content.

Raw execution outcomes belong to the tool contract family.

### 6. Compaction and context transforms

Compaction summaries, ctree transforms, and related context-shaping results must preserve causal and lineage clarity when represented in transcript form.

---

## Suggested Transcript Components

A first-pass transcript object should be able to represent:

- system/dev/user/assistant message items
- tool call items
- tool result items
- reasoning metadata items or linked metadata
- provenance refs
- event cursor / snapshot refs
- host-only annotation blocks

The contract should not force all hosts to render these identically.

---

## Non-Goals

This contract does not define:

- markdown logger formatting
- TUI renderer output shape
- OpenClaw event stream projection shape
- CLI bridge SSE chunk format
- storage engine implementation details

---

## Current Evidence

Current supporting evidence and tests include:

- `tests/test_agent_session.py`
- `tests/test_session_state_events.py`
- `tests/test_logging_v2_basic.py`
- `tests/test_todo_store.py`
- `tests/test_checkpoint_snapshot.py`
- `tests/test_permission_rule_persistence.py`

These now support a published transcript contract for the currently scoped kernel and host-bridge surfaces.

---

## Known Ambiguities

1. compaction and ctrees integration may still need deeper future treatment
2. richer reasoning metadata may still need additional cross-engine freezing
3. transcript and checkpoint interaction may grow more detailed for longrun-heavy runtimes

---

## First Schema Implications

The first-pass `bb.session_transcript.v1` schema should capture:

- transcript identity/version metadata
- ordered transcript items
- stable call ids and provenance fields
- host-only annotation support
- event cursor or snapshot metadata

It should avoid prematurely freezing a UI-specific rendering model, even as host/proving-ground transcript slices widen.

---

## Immediate Next Steps

1. define transcript item taxonomy
2. define required provenance fields
3. identify the minimal reasoning metadata model
4. define transcript derivation rules from kernel events
5. pair this contract with replay fixtures and checkpoint fixtures
