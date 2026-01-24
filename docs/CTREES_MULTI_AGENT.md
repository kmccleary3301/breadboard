# C‑Trees: Multi‑Agent / Subagents (Engine)

This document defines how multi-agent (“subagent”) lifecycle is represented in C‑Trees.

Design goals:
- Deterministic replay surface (no timestamps/random IDs in hashed payloads).
- Small, safe-by-default payloads (reference artifacts by path; don’t inline transcripts).
- Engine-owned mapping rules so the TUI can render without inventing semantics.

---

## Representation strategy (current)

We currently represent subagents via the existing C‑Trees node kind:
- `task_event`

`task_event` nodes are recorded from the session’s streamed `task_event` telemetry when:
- `ctrees.record_task_events.enabled: true`

This is intentionally minimal; it is not a transcript.

Optional enhancement:
- `subagent` nodes can be emitted once per aliased task when
  `ctrees.record_task_events.include_subagent_nodes: true`.

---

## `task_event` payload (engine-persisted)

The engine persists a minimal payload that can be used to build a deterministic subagent tree:

- `kind`: task lifecycle kind (e.g. `subagent_spawned`, `subagent_completed`, `subagent_failed`)
- `task_id`: a **stable alias** (see “Deterministic IDs” below)
- `parent_task_id`: optional stable alias for the parent task
- `tree_path`: optional stable alias path (e.g. `root/task_0001/task_0002`)
- `depth`: optional integer
- `priority`: optional
- `subagent_type`: e.g. `codex`, `claude`, `opencode` (string)
- `status`: lifecycle status (string)
- `artifact_path`: optional artifact path reference (string)

Notes:
- The persisted payload intentionally excludes large/secret-prone fields such as:
  - subagent transcripts
  - model prompts
  - `ctree_snapshot` blobs
- Runtime session/job ids are intentionally omitted from C‑Trees to keep replay surfaces stable.

Turn ordering guidance:
- If async task events include a `turn` field, the engine will record the event at that turn index.
- Prefer emitting completions at turn boundaries to keep ordering deterministic.

---

## Deterministic IDs

Runtime task identifiers may be non-deterministic (e.g., random IDs from provider/harness).

To keep C‑Trees replay-stable:
- the engine records a deterministic alias (first-seen order), and persists aliases in `task_id` / `parent_task_id`.
- the raw runtime IDs remain available in the main session event stream, but are not hashed into the C‑Tree payload.

This ensures:
- identical semantic runs produce identical C‑Trees hashes
- the UI can build a stable subagent tree without depending on random IDs

---

## TUI rendering guidance

The TUI should render subagent structure using the engine-provided tree view:
- `GET /sessions/{id}/ctrees/tree`

If additional grouping is needed, the engine can emit synthetic grouping nodes in the tree view (engine-owned).
