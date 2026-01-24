# C‑Trees: HTTP API (CLI Bridge)

This document describes the **engine-owned** C‑Trees HTTP surfaces exposed by the CLI bridge.

Goals:
- Provide a stable, minimal contract the client (TUI) can depend on.
- Keep the “tree semantics” engine-owned (clients render; the engine decides grouping/flags).
- Support both live sessions (in-memory) and replay/debug (disk / eventlog).

Non-goals (for now):
- A dedicated TUI “reducer bundle” shipped by the engine.
- A server-side incremental diff protocol beyond `ctree_node` (we treat it as the delta).

---

## Endpoints

All endpoints are under the CLI bridge server.

### `GET /sessions/{session_id}/ctrees`

Returns the latest in-memory C‑Trees snapshot and related metadata.

Response model: `CTreeSnapshotResponse`
- `snapshot`: `CTreeStore.snapshot()` (counts + node_hash)
- `last_node`: last seen node (if any)
- `compiler`, `collapse`, `runner`: engine metadata surfaces
- `hash_summary`: compact parity/UX summary
- `context_engine`: last applied context-engine metadata (optional)

Notes:
- This endpoint is intended for **metadata and resume bootstrapping**.
- It is not guaranteed to be disk-backed.

### `GET /sessions/{session_id}/ctrees/events`

Returns a page of C‑Trees events.

Query params:
- `source`: `auto | disk | eventlog | memory` (default: `auto`)
- `offset`: integer (default: `0`)
- `limit`: integer or null (default: null)
- `with_sha256`: boolean (default: false) — include an optional artifact sha256 for disk sources

Response model: `CTreeEventsResponse`
- `header`: parsed JSONL header line (if present)
- `events`: list of event objects, typically shaped like:
  - `{"kind": "...", "payload": {...}, "turn": 1, "node_id": "..."}`

Source behavior (best-effort):
- `disk`: reads `.breadboard/ctrees/meta/ctree_events.jsonl` (404 if absent)
- `eventlog`: derives from the session `events.jsonl` (if enabled/configured)
- `memory`: derives from the in-memory ring buffer of streamed `ctree_node` events
- `auto`: tries disk → eventlog → memory

### `GET /sessions/{session_id}/ctrees/tree`

Returns a **TUI-ready graph view** of the current C‑Trees store.

Query params:
- `source`: `auto | disk | eventlog | memory` (default: `auto`)
- `stage`: `RAW | SPEC | HEADER | FROZEN` (default: `FROZEN`)
- `include_previews`: boolean (default: false)

Response model: `CTreeTreeResponse`
- `nodes`: a flat list of nodes with `{id, parent_id, kind, turn, label, meta}`
- `root_id`: id of the root node in the list
- `selection`: compiler/collapse selection details (stable + compact)
- `hashes`: stable hashes for parity + diagnostics

Important:
- `include_previews=true` may include sanitized content previews in `meta`.
- Clients should treat `nodes` as an **ordered, deterministic** list, and render by `parent_id` pointers.

### `GET /sessions/{session_id}/ctrees/disk`

Returns disk artifact metadata for C‑Trees under candidate roots.

Query params:
- `with_sha256`: boolean (default: false) — sha256 can be expensive for large logs

Response model: `CTreeDiskArtifactsResponse`
- `root`: chosen root directory
- `artifacts`: map of known artifacts (exists, size, optional sha256)

---

## Recommended client flow (TUI)

### Initial load (live session)

1) Call `GET /sessions/{id}/ctrees` for initial metadata (optional, but recommended).
2) Connect to `GET /sessions/{id}/events` (SSE) and build the tree incrementally from `ctree_node` events.
3) Optionally call `GET /sessions/{id}/ctrees/tree` to obtain an engine-computed “render model” (recommended).

### Reconnect / resume (live session)

1) Persist the last seen SSE event id (or `seq`).
2) Reconnect to `GET /sessions/{id}/events` with `Last-Event-ID` header (or `from_id`/`from_seq`).
3) If the server returns HTTP 409 (`resume_window_exceeded`), fall back to:
   - showing metadata from `GET /sessions/{id}/ctrees`, and/or
   - fetching `GET /sessions/{id}/ctrees/tree?source=disk` if artifacts exist.

### Replay / debug (disk artifacts)

1) Call `GET /sessions/{id}/ctrees/disk` to see what exists.
2) Use `GET /sessions/{id}/ctrees/tree?source=disk&stage=FROZEN` for a stable view.
3) Use `GET /sessions/{id}/ctrees/events?source=disk` to inspect raw C‑Trees events.

---

## Relationship to SSE events

The main session event stream includes:
- `ctree_node` — treated as the append-only delta event (node + snapshot)
- `ctree_snapshot` — a run-end summary containing compiler/collapse/hash_summary/context_engine (when available)

The TUI may either:
- consume `ctree_node` directly into its own reducer, or
- call `GET /sessions/{id}/ctrees/tree` periodically (or on demand) and render the engine-owned view.

