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
- `agentic_coder_prototype/run_logging/run_logger.py`
- `agentic_coder_prototype/run_logging/request_recorder.py`
- `agentic_coder_prototype/run_logging/api_recorder.py`
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
- coordination signal lifecycle
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
- `coordination.signal`
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

These now support a frozen kernel event envelope plus a tracked event-family registry for the currently scoped surfaces.

---

## Known Ambiguities

1. some longrun/task payload families may still grow richer over time
2. projection adapters may still expose additional convenience fields above kernel truth
3. future backend-specific event families must still be classified before they are treated as kernel truth

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

That kind-specific specificity should be layered in only when new families are added or existing families gain additional frozen semantics.

---

## Immediate Next Steps

1. keep the event-family registry aligned with future emitter additions
2. classify any new backend-mediated families before they are treated as kernel truth
3. expand family-specific payload semantics only when new runtime slices require it
4. define first comparator class for event traces
5. create example fixtures under `conformance/engine_fixtures/`
