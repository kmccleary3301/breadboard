# C‑Trees: Tree View Payload (Engine → Client Contract)

The C‑Trees “tree view” is an **engine-owned** render model designed to minimize TUI complexity.

Clients should:
- treat node IDs as **opaque strings**
- render by `parent_id` pointers
- avoid re-deriving engine semantics (selection, collapse flags, grouping) client-side

The tree view is available via:
- `GET /sessions/{session_id}/ctrees/tree`

---

## Response shape

The response model is `CTreeTreeResponse`:

- `source`: where the data came from (`disk|eventlog|memory`)
- `stage`: `RAW|SPEC|HEADER|FROZEN`
- `root_id`: node id of the root
- `nodes`: a flat list of `CTreeTreeNode`
- `selection`: selection metadata (optional)
- `hashes`: stable hashes (optional)

Each `CTreeTreeNode` contains:
- `id`: stable id (string)
- `parent_id`: stable parent id (string or null)
- `kind`: node kind (string)
- `turn`: integer turn index (or null)
- `label`: short display label (optional)
- `meta`: dict of node metadata (stable keys; values may be null)

---

## Node kinds

Tree view node kinds are **rendering kinds** (not the same as raw C‑Tree node vocabulary).

Current kinds include:
- `root` — single root node (id=`ctrees:root`)
- `turn` — one per observed turn index (id=`ctrees:turn:{turn}`)
- `task_root` — synthetic root for multi-agent grouping (id=`ctrees:tasks`)
- `task` — synthetic per-task grouping node (id=`ctrees:task:{task_id}`)
- leaf kinds reflecting recorded C‑Tree nodes:
  - `message`, `lifecycle`, `guardrail`, `task_event`, `transcript`, ...
- `collapsed` — a synthetic grouping node emitted for `HEADER|FROZEN` stages

---

## Determinism and ordering guarantees

The engine guarantees:
- `nodes` is emitted in a deterministic order:
  1) root
  2) turn group nodes (ascending turn)
  3) optional task grouping nodes (stable first-seen order)
  4) leaves in original append order (after stage filtering)
  5) synthetic `collapsed` summary nodes (deterministic order)
- `id` values are deterministic given the same underlying C‑Trees event stream.
- `meta` flags (`selected|kept|dropped|collapsed`) are deterministic given the same compiler/collapse config and stage.

Clients should not attempt to “sort” nodes except for UI presentation; the engine ordering is stable and safe to preserve.

---

## `meta` fields (common)

All leaf nodes include stable selection/collapse flags:
- `selected`: boolean
- `kept`: boolean
- `dropped`: boolean
- `collapsed`: boolean

Most leaf nodes also include:
- `digest`: stable semantic digest of `{kind,turn,payload}`

Message nodes may include (when available):
- `role`, `name`
- `payload_hash`, `content_hash`, `content_len`
- `tool_call_count`
- when `include_previews=true`:
  - `content_preview`, `content_preview_truncated`, `content_preview_redacted`

Non-message nodes include:
- `payload_sha1` (hash of sanitized payload)

Synthetic `collapsed` nodes include:
- `collapsed_ids`: list of collapsed message node ids
- `collapsed_sha256`: stable hash over `collapsed_ids`

---

## `hashes` fields

The response `hashes` is a compact parity/diagnostic surface. Current fields include:
- compiler hashes: `z1`, `z2`, `z3`
- `node_hash`: aggregate hash of recorded node digests
- `tree_sha256`: stable hash over the emitted tree-view node ids

---

## Client usage patterns

Recommended:
- Use `GET /ctrees/tree` as the primary UI model for rendering.
- Use `GET /ctrees/events` or streamed `ctree_node` only for inspection/debugging and incremental updates.

Avoid:
- interpreting the raw C‑Tree as a strict parent/child tree (today it is a timeline with turn grouping).
- relying on message content previews unless explicitly requested (previews are optional and redacted).
