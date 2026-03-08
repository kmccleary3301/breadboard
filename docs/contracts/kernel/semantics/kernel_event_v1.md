# Kernel Event V1

## Purpose

`kernel_event_v1` is the canonical runtime event envelope for the multi-engine BreadBoard program.

This contract is intended to be the primary runtime truth surface from which:

- transcript/session state is derived
- replay sessions are reconstructed or validated
- conformance evidence is organized
- host projections are produced

This document defines the **semantic contract**, not just the JSON shape.

---

## Contract Role

The kernel event log should answer:

- what happened
- in what order
- on behalf of which run/session/task/turn/step/call
- who emitted the event
- whether the event is model-visible, host-visible, or audit-only
- what later events it causally relates to

The kernel event log is not a UI event stream and not a logging convenience format.

---

## Current Python Owners

Primary current owners and related surfaces:

- `agentic_coder_prototype/engine.py`
- `agentic_coder_prototype/agent_session.py`
- `agentic_coder_prototype/turn_context.py`
- `agentic_coder_prototype/logging_v2/run_logger.py`
- `agentic_coder_prototype/logging_v2/request_recorder.py`
- `agentic_coder_prototype/logging_v2/api_recorder.py`
- `agentic_coder_prototype/ctrees/events.py`
- task and CLI bridge event normalizers tested elsewhere

These owners imply that event semantics are currently distributed across runtime, logging, replay, and projection-adjacent paths.

---

## Shared Semantics That Must Be Frozen

### 1. Canonical identity

Every event must have stable identity and lineage anchors:

- event id
- run id
- session id
- sequence number
- timestamp
- actor
- event kind

Optional but important lineage anchors:

- turn id
- step id
- task id
- call id
- caused-by event id

### 2. Ordering

Event ordering must be deterministic within a run.

The contract should assume:

- monotonic `seq`
- append-only semantics
- causal order preserved for replay-significant surfaces

### 3. Visibility

Every event must be classifiable by visibility semantics.

Initial visibility classes:

- `model`
- `host`
- `audit`

This classification is needed because not every runtime fact belongs in the transcript and not every transcript-visible fact is sufficient for audit or replay.

### 4. Event family coverage

At minimum, the event family must cover:

- run lifecycle
- turn lifecycle
- step lifecycle
- model request/delta/completion
- tool call lifecycle
- permission request/decision
- task/subagent/background lifecycle
- compaction/memory lifecycle where applicable
- checkpoint/save/restore lifecycle
- warning/limit/update surfaces

### 5. Payload semantics

`payload` must be kind-specific, but the envelope must stay stable.

The event envelope should not change casually when a client wants a new convenience field.

---

## First-Pass Event Families

The current first-pass recommended family set is:

- `run.started`
- `run.completed`
- `run.failed`
- `turn.started`
- `turn.completed`
- `step.started`
- `step.completed`
- `model.requested`
- `model.delta`
- `model.completed`
- `tool.called`
- `tool.approval_requested`
- `tool.approved`
- `tool.denied`
- `tool.started`
- `tool.progress`
- `tool.completed`
- `tool.failed`
- `tool.cancelled`
- `permission.requested`
- `permission.decided`
- `task.spawned`
- `task.progress`
- `task.sleeping`
- `task.wakeup`
- `task.join_requested`
- `task.joined`
- `task.completed`
- `task.failed`
- `compaction.started`
- `compaction.completed`
- `checkpoint.saved`
- `checkpoint.restored`
- `warning`
- `limits.updated`

This list is intentionally explicit because event-family sprawl is one of the fastest ways to lose control of a multi-engine system.

---

## Non-Goals

This contract does not define:

- the exact UI reducer event shapes
- TUI task list events
- CLI bridge SSE convenience envelopes
- OpenClaw projection events
- webapp-specific projection events

Those should be projection adapters derived from kernel events.

---

## Current Evidence

Current supporting evidence and tests include:

- `tests/test_session_state_events.py`
- `tests/test_task_streaming_events.py`
- `tests/test_cli_bridge_task_event_normalization.py`
- `tests/test_cli_backend_streaming.py`
- `docs/conformance/event_envelope_snapshots_baseline_v1.json`
- `docs/conformance/projection_surface_manifest_v1.json`

These prove that event and projection behavior matters today, but not yet that a single kernel event contract is frozen.

---

## Known Ambiguities

1. kernel and projection events are not yet cleanly separated in practice
2. event families are not yet published as one canonical list
3. visibility semantics are not yet formalized consistently
4. some longrun/task events may still be represented differently in different subsystems

---

## First Schema Implications

The first-pass schema for `bb.kernel_event.v1` should prioritize:

- envelope stability
- required identity fields
- visibility
- actor
- kind
- generic payload container

It should **not** attempt to fully encode every kind-specific payload in the first pass.

That kind-specific specificity should be layered in once the family list and semantic dossiers are stable.

---

## Immediate Next Steps

1. freeze the event family list
2. audit current emitters against that list
3. identify projection-only events and demote them from kernel status
4. define first comparator class for event traces
5. create example fixtures under `conformance/engine_fixtures/`
