# C‑Trees: Compiler Stages Contract (RAW / SPEC / HEADER / FROZEN)

This document defines the intended contract for the C‑Trees compiler stages.

Why stages exist:
- The engine records a **safe-by-default** event stream (`ctree_node`) that is *not* a prompt-ready structure.
- The compiler turns that stream into progressively more “prompt-shaped” representations.
- Collapse policy can drop/collapse content deterministically without relying on timestamps.

Non-negotiables:
- Deterministic across replays given identical semantic inputs.
- No timestamps/random IDs in stage payloads.
- Stable ordering (append order for nodes; explicit sorting where needed).

---

## Where stages appear

Stages are surfaced in:
- `ctree_snapshot.payload.compiler.stages.{RAW|SPEC|HEADER|FROZEN}`
- `GET /sessions/{id}/ctrees` (same structure in `compiler`)
- `GET /sessions/{id}/ctrees/tree` (via `selection` and `hashes`)

---

## Common fields

Every stage payload should include:
- `schema_version` (string)

The compiler output object should include:
- `schema_version`
- `hashes` (at least `z1`, `z2`, `z3`)
- `stages` (dict of stage payloads)

Notes:
- The stage payloads are designed to be compact. They should not embed large transcripts or raw tool results.

---

## Stage definitions

### RAW

Purpose:
- A small “inventory” of what exists in the C‑Tree without selecting anything.

Allowed contents:
- counts (`node_count`, `event_count`)
- kind histogram (`kind_counts`)
- stable aggregate hashes (`node_hash`)
- optional prompt summary reference (if present)

Must NOT contain:
- message bodies / tool outputs
- secrets

### SPEC

Purpose:
- A deterministic “selection spec” that enumerates which nodes are candidates and why.

Allowed contents:
- selection config echo (kind allowlist, max_nodes/max_turns, etc.)
- `selected_ids` (append-order ids of selected nodes)
- `selection_sha256` (hash of the selection summary + ids)
- a deterministic list of selected node references:
  - id, digest, kind, turn, payload_hash/payload_shape

Must NOT contain:
- full payload bodies (except tiny safe summaries)

### HEADER

Purpose:
- A prompt-ready “header materialization” (hashes and optional sanitized previews).

Allowed contents:
- `messages`: a deterministic list of message items, each referencing a source node id
- hash-only mode (default): include hashes/lengths/tool-call counts
- sanitized preview mode (debug/opt-in): include truncated, redacted previews
- `selection_sha256` (to tie header materialization to a SPEC selection)

Must NOT contain (by default):
- raw secrets
- unbounded message content

### FROZEN

Purpose:
- The replay/parity surface intended to remain stable and compact.

Current behavior:
- FROZEN may mirror HEADER initially.

Intended behavior:
- FROZEN is the “what was used” surface for deterministic replays.
- If previews are included in HEADER for debugging, FROZEN should remain hash-focused unless explicitly configured.

---

## Ordering rules

- Node selection ordering is append-order unless explicitly documented otherwise.
- Any set-like outputs (e.g., kind counts) must be emitted in a deterministic key order.
- Tie-breakers must never use time; prefer `(turn, ordinal, digest, id)` style stable keys.

---

## Collapse policy config (engine)

The deterministic collapse policy accepts:
- `target` (int or null): maximum number of nodes to keep (drop oldest first).
- `mode` (string): `none` or `all_but_last` (collapse all but the most recent retained node).
- `kind_allowlist` (optional list): restrict collapse to specific kinds.

Stage behavior (current policy):
- `RAW`: no drop/collapse
- `SPEC`: drop only (oldest first if over target)
- `HEADER`: drop + collapse (depending on mode)
- `FROZEN`: mirrors HEADER (intended parity surface)
