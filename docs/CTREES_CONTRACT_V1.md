# Breadboard C-Trees Contract V1 (Engine ↔ TUI)

This document defines the **minimal** contract required to wire C-Trees into the Breadboard TUI. It is intentionally small and stable so that we can iterate on C-Trees without breaking parity-critical UX.

## 1) Event Envelope (shared)

All C-Tree events must follow the canonical envelope:

```
{
  "id": "opaque-resume-token",
  "seq": 123,
  "timestamp_ms": 1700000000000,
  "type": "ctree_snapshot" | "ctree_node" | "task_event",
  "session_id": "session-uuid",
  "run_id": "optional-run-id",
  "turn_id": "optional-turn-id",
  "thread_id": "optional-thread-id",
  "data": { ... }
}
```

Rules:
- `seq` is **monotonic** within a session and is used for ordering.
- `id` is only used to resume streams and is **not** an ordering key.
- `data` is the canonical payload (legacy `payload` is tolerated during migration).

## 2) C-Tree Events

### 2.1 `ctree_snapshot`

Represents a snapshot merge. Any field may be omitted when unchanged.

`data` payload:
```
{
  "snapshot": { "node_count": 123, "node_hash": "abc..." },
  "compiler": { "z1": "...", "z2": "...", "z3": "..." },
  "collapse": { "policy": "drop-low-score", "dropped": 42 },
  "runner": { "phase": "rollout", "status": "running" },
  "last_node": { "id": "node-...", "score": 0.71 }
}
```

Notes:
- The TUI merges these fields into the current C-Tree summary.
- Missing keys must **not** clear existing state.

### 2.2 `ctree_node`

Incremental node update (for streaming or frequent updates).

`data` payload:
```
{
  "snapshot": { "node_count": 124, "node_hash": "def..." },
  "node": { "id": "node-...", "score": 0.73 }
}
```

Notes:
- The TUI merges `snapshot` and `node` into the same summary view.

### 2.3 `task_event` (ctree metadata)

Task events can carry C-Tree metadata so the tasks panel can reference it.

`data` payload (subset):
```
{
  "task_id": "task-xyz",
  "status": "running",
  "ctree_node_id": "node-abc",
  "ctree_snapshot": { ...same shape as snapshot... }
}
```

Notes:
- The tasks panel shows `ctree_node_id` and a compact C-Tree summary when enabled.

## 3) REST Endpoint (optional but recommended)

**GET** `/sessions/{id}/ctrees`

Response (compatible with TUI types):
```
{
  "snapshot": { ... },
  "compiler": { ... },
  "collapse": { ... },
  "runner": { ... },
  "last_node": { ... }
}
```

Notes:
- Used to hydrate the UI when connecting to long-running sessions.

## 4) TUI Gating (config)

The C-Tree UI is **disabled by default** except on `breadboard_v1` profile. It can be controlled with:

```
BREADBOARD_CTREES_ENABLED=1
BREADBOARD_CTREES_SUMMARY=1
BREADBOARD_CTREES_TASK_NODE=1
```

User config override (`~/.breadboard/config.json`):
```
{
  "ctrees": {
    "enabled": true,
    "showSummary": true,
    "showTaskNode": true
  }
}
```

## 5) Ordering Expectations

- C-Tree events may arrive interleaved with tool/assistant events.
- The TUI **only** uses `seq` for ordering; timestamps are informational.
- If both `ctree_node` and `ctree_snapshot` arrive, the most recent `seq` wins.

## 6) Non-goals (V1)

- No branch switching UI yet.
- No visual tree render (graph view) yet.
- No backfills beyond `GET /sessions/{id}/ctrees`.
