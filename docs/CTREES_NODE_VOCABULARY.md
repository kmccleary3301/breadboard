# C‑Trees: Node Vocabulary (Engine)

This document defines the **current** (C‑Trees Phase 2/3) node vocabulary that the engine records into the C‑Tree
event stream (`ctree_node`) and persists to disk (`.breadboard/ctrees/meta/ctree_events.jsonl`).

The vocabulary is intentionally small right now. The primary goal is **deterministic replay** and providing a stable
surface for future “compiler → collapse → context selection” work.

## What is a “node” today?

In the current engine, a C‑Tree node is an append-only record produced by `SessionState._record_ctree()`:

- `kind`: a string label from the vocabulary below
- `turn`: an optional integer turn index (when available)
- `payload`: a JSON-serializable object (sanitized for determinism + safety)
- `digest`: a stable SHA‑1 digest of the canonicalized `{kind, turn, payload}`
- `id`: a stable, compact **unique** id derived from `(ordinal, digest_prefix)`
  - this ensures repeated identical payloads produce distinct nodes while remaining deterministic across replays

### `ctree_node` payload schema (stream contract)

Each `ctree_node` SSE event payload must contain:

- `node` (object, required)
  - `id` (string, required)
  - `digest` (string, required)
  - `kind` (string, required)
  - `turn` (integer or null, optional)
  - `payload` (any, required)
- `snapshot` (object, required)
  - `schema_version` (string, required)
  - `node_count` (integer, required)
  - `event_count` (integer, required)
  - `last_id` (string or null, required)
  - `node_hash` (string or null, required)

Authoritative JSON schema:
`docs/contracts/cli_bridge/schemas/session_event_payload_ctree_node.schema.json`

Nodes are **not yet a true tree** (no parent/children pointers). “Tree-ness” is currently provided by:
- turn grouping (`turn`)
- semantic grouping inside the compiler/collapse stages (future)

## Determinism + safety rules (must-haves)

All node hashing/persistence uses canonicalization from `agentic_coder_prototype/ctrees/schema.py`:

- volatile keys removed: `seq`, `timestamp`, `timestamp_ms`
- obvious secret keys redacted (key-based): `api_key`, `authorization`, etc.
- JSON is serialized with stable key ordering (`sort_keys=True`) and stable separators

When adding new node kinds, ensure you **do not** introduce nondeterminism via timestamps, random ids, or unordered
maps that aren’t canonicalized.

## Vocabulary (current)

### `lifecycle`

Recorded from `SessionState.record_lifecycle_event()` to capture lifecycle transitions and structured metadata.

Payload shape (current):
- `type`: lifecycle marker (string)
- `payload`: arbitrary object
- `turn` (optional): turn index (when applicable)
- `timestamp` (volatile; ignored for hashing)
- `seq` (volatile; ignored for hashing)

### `message`

Recorded from `SessionState.add_message()` for each message added to state (user, assistant, or tool role).

Payload shape (current):
- `role`: `"user" | "assistant" | "tool" | ...`
- `content`: message content (string or rich payload, depending on provider adapter)
- `tool_calls`: optional tool-call list attached to assistant messages
- `name`: optional name field (provider-specific)

Notes:
- This is deliberately a **summary** payload (not the full provider message object).
- Tool calls/results are still emitted on the main session stream; the C‑Tree captures the message-level context.

### `transcript`

Recorded from `SessionState.add_transcript_entry()` for the engine transcript stream.

Payload shape: whatever is appended to `SessionState.transcript` (engine-owned).

### `guardrail`

Recorded from `SessionState.record_guardrail_event()` to preserve guardrail telemetry.

Payload shape (current):
- `type`: guardrail event type (string)
- `payload`: arbitrary object
- `turn`: current turn index
- `timestamp` (volatile; ignored for hashing)
- `seq` (volatile; ignored for hashing)

### `task_event` (optional; feature-gated)

Recorded from `SessionState.emit_task_event()` when `ctrees.record_task_events.enabled` is enabled.

This is intentionally **minimal** and is meant to represent multi-agent/subagent lifecycle in a replay-stable way
without persisting large prompts or transcripts.

Payload shape (current):
- `kind`: task event kind (e.g. `subagent_spawned`, `subagent_completed`)
- `task_id`: **stable alias** (string, e.g. `task_0001`)
- `parent_task_id`: optional stable alias (string, `root` is preserved)
- `tree_path`: optional hierarchical alias path (string, e.g. `root/task_0001`)
- `depth`: optional depth (int)
- `priority`: optional priority (string/number)
- `subagent_type`: agent type (string)
- `status`: lifecycle status (string)
- `artifact_path`: optional stable alias path hint (string; may not match on-disk path)

### `subagent` (optional; feature-gated)

Recorded from `SessionState.emit_task_event()` when:
- `ctrees.record_task_events.enabled: true`
- `ctrees.record_task_events.include_subagent_nodes: true`

Emitted once per aliased task id to provide a stable subtree anchor.

Payload shape (current):
- `task_id`: **stable alias** (string)
- `parent_task_id`: optional stable alias (string)
- `tree_path`: optional hierarchical alias path (string)
- `subagent_type`: agent type (string)
- `status`: lifecycle status (string)

## Mapping notes (current behavior)

- The engine records the vocabulary above directly (via `_record_ctree`) and emits a `ctree_node` SSE event for each
  recorded node.
- The TUI should treat `ctree_node` as an append-only delta: it contains enough information to reconstruct the node
  timeline incrementally.
- At run completion, the engine emits a `ctree_snapshot` SSE event containing:
  - `snapshot`: `CTreeStore.snapshot()` summary (counts + node_hash)
  - `compiler`, `collapse`, `runner`: engine metadata surfaces (may be stubbed)

### Source → node mapping (engine)

Current mapping is intentionally direct and deterministic:

- `SessionState.add_message(...)` → `kind="message"`
- `SessionState.add_transcript_entry(...)` → `kind="transcript"`
- `SessionState.record_guardrail_event(...)` → `kind="guardrail"`
- `SessionState.record_lifecycle_event(...)` → `kind="lifecycle"`
- `SessionState.emit_task_event(...)` → `kind="task_event"` (feature-gated)
- `SessionState.emit_task_event(...)` → `kind="subagent"` (feature-gated, optional per `include_subagent_nodes`)

If additional sources are added, document them here and add a test in
`tests/test_ctree_mapping_rules.py` (or similar).

## Extending the vocabulary (guidelines)

When adding a new kind, document it here and follow these rules:

1) Prefer semantic, stable payloads (avoid raw provider objects).
2) Avoid persistence of secrets; rely on schema redaction as a backstop, not a primary defense.
3) Keep payloads small; large blobs should be referenced by artifact path, not embedded.
4) Do not rely on implicit ordering from dict iteration; canonicalization must make it stable.
