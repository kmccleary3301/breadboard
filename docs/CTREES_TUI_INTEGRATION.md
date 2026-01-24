# C‑Trees: TUI Integration Guide (Engine → TUI)

This guide is written for the TUI engineer integrating C‑Trees into the Breadboard UI.

Key idea:
- The engine exposes an **engine-owned** “tree view” payload so the TUI does not need to invent C‑Tree semantics.

Hard constraints:
- Treat all C‑Trees IDs as **opaque strings**.
- Assume reconnects can exceed the in-memory resume window.

---

## What to consume

You have two complementary sources of truth:

### 1) SSE stream (`/sessions/{id}/events`)

Relevant event types:
- `ctree_node` — append-only delta (node + store snapshot metadata)
- `ctree_snapshot` — run-end summary (compiler/collapse/hash_summary/context_engine)

Use this for:
- “live” incremental updates while the engine is running
- lightweight metadata displays (counts / hashes)

### 2) Engine tree view (`/sessions/{id}/ctrees/tree`)

Use this for:
- rendering the tree view UI (expand/collapse, node inspector)
- stage toggles (`RAW|SPEC|HEADER|FROZEN`)
- safe preview toggles (`include_previews=true`)

The tree view is intentionally client-ready:
- `nodes` is a flat list; render by `parent_id`
- selection flags (`selected|kept|dropped|collapsed`) are precomputed
- collapse summaries are emitted as synthetic nodes (when applicable)

See:
- `docs/CTREES_HTTP_API.md`
- `docs/CTREES_TREE_VIEW.md`

---

## Recommended UI flow

### Initial load (live session)

1) Call `GET /sessions/{id}/ctrees` to show quick metadata (optional).
2) Start SSE subscription to `GET /sessions/{id}/events`.
3) Render Tree View by calling `GET /sessions/{id}/ctrees/tree`:
   - default `stage=FROZEN`
   - default `include_previews=false`

### Live updates

On receiving `ctree_node`:
- Option A (simple): throttle (e.g. 5–10 Hz) and re-fetch `/ctrees/tree` to refresh the view.
- Option B (more work): update a client reducer from `ctree_node` and only call `/ctrees/tree` on demand.

Given the engine already provides a render model, Option A is usually sufficient and keeps the UI deterministic.

### Reconnect / resume

1) Persist the last seen SSE event id/seq.
2) Reconnect with `Last-Event-ID`.
3) If the server returns HTTP 409 (`resume_window_exceeded`):
   - fall back to `GET /sessions/{id}/ctrees/tree?source=disk` if artifacts exist, else
   - show metadata only (tree view partial/unavailable) until the session completes.

---

## Stage toggles

Offer a UI toggle (or debug menu) for:
- `RAW` (everything)
- `SPEC` (policy-driven selection)
- `HEADER` / `FROZEN` (prompt-ready/collapsed view)

Note:
- `HEADER` and `FROZEN` are expected to be the most useful for “what context was selected”.

---

## Preview safety

`include_previews=true` may include sanitized content previews.

Default recommendation:
- previews OFF by default
- previews ON behind an explicit user action (debug mode / “show previews” toggle)

---

## Fixtures you can use for reducer tests

Engine-committed fixtures exist under:
- `tests/fixtures/ctrees/`

For example:
- `tests/fixtures/ctrees/simple_ctree_events.jsonl`
- `tests/fixtures/ctrees/simple_ctree_tree_frozen.json`

These are intended to support deterministic reducer and UI snapshot tests.

