# TUI TODO Event Contract (`payload.todo`)

This document defines the **additive** `payload.todo` field that may appear on CLI bridge session events (most commonly `tool_call` and `tool_result`). The Breadboard Ink TUI consumes this field to render:

- The live TODO preview above the composer input.
- The TODOs overlay (and scope switching).

The contract is intentionally **forward compatible**: unknown fields must be ignored by clients.

## Where It Is Produced

The canonical normalization/projection boundary for CLI bridge events is:

- `agentic_coder_prototype/api/cli_bridge/session_runner.py`
  - `SessionRunner._translate_runtime_event(...)`
  - `SessionRunner._normalize_tool_call_payload(...)`
  - `SessionRunner._normalize_tool_result_payload(...)`

The TUI currently accepts either:

1. A direct array snapshot (`payload.todo = [...]`)
2. A structured envelope (`payload.todo = { op, revision, ... }`)

## Schema

The exported JSON schema lives in:

- `docs/contracts/cli_bridge/schemas/session_event_payload_todo_update.schema.json`

It is referenced from:

- `docs/contracts/cli_bridge/schemas/session_event_payload_tool_call.schema.json`
- `docs/contracts/cli_bridge/schemas/session_event_payload_tool_result.schema.json`

## Revision Semantics

If `revision` is present, it must be a **monotonic integer per scope**:

- Monotonic per `scopeKey` (or inferred scope).
- Strictly increasing for authoritative updates.

Client rule:

- Ignore any update with `revision <= lastSeenRevision(scopeKey)`.
- Once a numeric revision has been observed for a scope, ignore **unversioned** fallbacks for that scope (so stale tool-parsed snapshots cannot override authoritative state).

## Scope Semantics

TODO updates can be scoped:

- `scopeKey`: stable identifier for the TODO list scope.
- `scopeLabel`: human-friendly label for display.

If scope fields are absent, clients may infer scope from event payload fields such as `lane_id`, `task_id`, or `agent_id`. If no scope can be inferred, scope defaults to `main`.

## Operations

The preferred representation is an **envelope**:

```json
{
  "todo": {
    "op": "replace",
    "revision": 12,
    "scopeKey": "main",
    "scopeLabel": "main",
    "items": [
      { "id": "t-1", "title": "Ship contract", "status": "done" },
      { "id": "t-2", "title": "Add tests", "status": "in_progress" }
    ]
  }
}
```

Supported ops (aliases allowed; see schema):

- `replace` / `snapshot`: full list replacement (ordering is authoritative)
- `clear` / `reset`: clear all items in scope
- `patch` / `update`: patch existing items by id
- `upsert` / `add`: insert or update a single item
- `delete` / `remove`: delete by id(s)

### Replace

```json
{
  "op": "replace",
  "revision": 5,
  "scopeKey": "lane-a",
  "items": [
    { "title": "Run tests", "status": "in_progress" },
    { "title": "Fix failures", "status": "todo" }
  ]
}
```

### Patch

```json
{
  "op": "patch",
  "revision": 6,
  "scopeKey": "lane-a",
  "patches": [
    { "id": "t-2", "status": "done" }
  ]
}
```

### Delete

```json
{
  "op": "delete",
  "revision": 7,
  "scopeKey": "lane-a",
  "ids": ["t-2"]
}
```

## Snapshot-On-Reconnect (Recommended)

When a client reconnects and may have missed history, the projection layer should emit a `replace` snapshot for each known scope so clients can converge without relying on fragile backfills.

## Notes For Client Implementations

- Do not infer TODOs from assistant prose by default.
- Prefer `payload.todo` when present.
- Treat tool payload parsing as a fallback only.

