# C‑Trees: Persistence + Replay (Engine)

This document captures the **current** (Phase 1) C‑Trees persistence/replay contract in the Breadboard engine.

Goals:
- Deterministic hashes across replays.
- A small, purpose-built artifact set for UI + debugging.
- Safe-by-default persistence (avoid writing secrets).

## Authoritative replay source (current decision)

**Authoritative source for C‑Trees replay is the C‑Trees event log:**

- `.breadboard/ctrees/meta/ctree_events.jsonl`

This is intentionally separate from the engine’s general session eventlog, because:
- it is smaller and purpose-built for tree reconstruction,
- it can enforce its own schema versioning,
- it can apply “safe by default” persistence rules (redaction/sanitization) without affecting other logs.

If `.breadboard/ctrees/meta/ctree_events.jsonl` is absent, `CTreeStore.from_dir()` falls back to legacy `events.jsonl` (best-effort).

## Artifact layout

All paths below are relative to the **workspace directory**.

- `.breadboard/ctrees/meta/ctree_events.jsonl` — line-delimited JSON event log
- `.breadboard/ctrees/meta/ctree_snapshot.json` — latest summary snapshot

Notes:
- The `.breadboard/` directory is engine-owned and is git-ignored in this repo.
- The engine writes these artifacts at run completion by default; see “Engine write behavior” below.

### `.breadboard/ctrees/meta/ctree_events.jsonl` format

The file begins with a **header line**:

```json
{"_type":"ctree_eventlog_header","schema_version":"0.1"}
```

Subsequent lines are events, typically shaped like:

```json
{"kind":"message","payload":{...},"turn":1,"node_id":"abc123..."}
```

Notes:
- The header line is ignored by loaders.
- Unknown/empty entries (missing `kind`) are ignored by loaders.

### `.breadboard/ctrees/meta/ctree_snapshot.json` format

This is a single JSON object, currently produced by `CTreeStore.snapshot()`, including:
- `schema_version`
- `node_count`, `event_count`, `last_id`
- `node_hash` (stable aggregate hash over recorded node digests)

## Determinism + safety guarantees

### Hashing / digests

Node digests are computed from a canonical JSON representation:
- volatile keys like `seq` / timestamps are removed
- obvious secret keys are redacted (key-based)

This ensures that:
- replaying the same “semantic” events produces the same hashes
- differing API keys/tokens do not affect C‑Trees hashes

### Persistence redaction (default)

By default, `CTreeStore.persist()` writes **sanitized** events:
- volatile keys removed
- secret keys redacted

There is an opt-in escape hatch for local-only debugging:

```py
store.persist(workspace_dir, include_raw=True)
```

## Loading / replay API

- `CTreeStore.from_dir(workspace_dir / ".breadboard/ctrees")` loads `meta/ctree_events.jsonl` (or legacy `events.jsonl`) and reconstructs an in-memory store.
- `CTreeStore.from_events(events)` reconstructs an in-memory store from a list/iterable of event dicts.

## Engine write behavior

At run completion, the engine persists a C‑Trees event log + snapshot to:

- `workspace/.breadboard/ctrees/`

Config (engine config YAML):

```yaml
ctrees:
  persist:
    enabled: true          # default: true
    root: .breadboard/ctrees   # optional override (workspace-relative)
    include_raw: false     # default: false (writes sanitized events)
```

If `ctrees.persist.enabled` is set to `false`, no C‑Trees artifacts are written to disk.

## Relationship to streaming + `/sessions/{id}/ctrees`

The CLI bridge exposes:
- streaming events: `ctree_node` and `ctree_snapshot`
- snapshot endpoint: `GET /sessions/{session_id}/ctrees`
 - disk metadata endpoint: `GET /sessions/{session_id}/ctrees/disk` (paths + sizes; optional sha256)

Current behavior:
- `ctree_node` is emitted as nodes are recorded (append-only).
- a summary `ctree_snapshot` is emitted near run completion (compiler/collapse metadata).
- `/sessions/{id}/ctrees` returns the most recent in-memory snapshot/last node; it is not currently a disk-backed API.

### Resume semantics (snapshot + streamed deltas)

Recommended TUI strategy:

1) **Initial load**
   - Call `GET /sessions/{id}/ctrees` to get the latest snapshot metadata (and last node, if present).
   - Connect to `GET /sessions/{id}/events` and build the tree from streamed `ctree_node` events.

2) **Reconnect / resume**
   - Persist the last seen SSE event id (or `seq`) on the client.
   - Reconnect to `GET /sessions/{id}/events` with `Last-Event-ID` (or `from_id`) set.
   - If the engine returns HTTP 409 (`resume_window_exceeded`), fall back to displaying metadata from
     `GET /sessions/{id}/ctrees` and (if available) render a disk-backed tree via
     `GET /sessions/{id}/ctrees/tree?source=disk`.

Engine note:
- Resume validation can use a **disk fallback** when `BREADBOARD_EVENTLOG_RESUME=1` and the eventlog is enabled,
  since `ctree_node` events are part of the session event stream.

### `ctree_node` vs `ctree_delta` (naming decision)

For Phase 2, **`ctree_node` is treated as the canonical “delta” event**: each event contains enough information
to reconstruct the tree incrementally (node + snapshot).

`ctree_delta` is reserved for a future compact delta schema, but is not currently part of the streaming envelope.

## Backfill utility (from session eventlog)

If you have a session `events.jsonl` (the engine eventlog) that contains `ctree_node` events, you can backfill a
`.breadboard/ctrees/` artifact set using:

```bash
python scripts/backfill_ctrees_from_eventlog.py --eventlog /path/to/events.jsonl --out /path/to/workspace/.breadboard/ctrees
```

Notes:
- This is best-effort: it extracts only `ctree_node` events.
- It is deterministic given a fixed input log and preserves stored `node_id` values.
- Backfilled snapshots are marked with `backfilled_from_eventlog: true` and should be treated as **non-authoritative**
  for parity unless explicitly allowed.
- The C‑Trees hash summary will omit `node_hash` when `backfilled_from_eventlog` is present.

## Future direction

Later phases will define:
- whether `ctree_node` remains the canonical delta event, or a dedicated `ctree_delta` schema becomes primary,
- stable “node vocabulary” + mapping rules from session events (see `docs/CTREES_NODE_VOCABULARY.md` for the current vocabulary),
- a feature-gated hook that uses the compiled tree to drive context selection (`before_model`).

See also:
- `docs/CTREES_CONTEXT_ENGINE.md`
