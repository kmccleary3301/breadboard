# C‑Trees: Schema and Migration Notes

This document captures the migration posture for C‑Trees artifacts under `.breadboard/ctrees/`.

Primary goals:
- preserve determinism and replay stability
- keep clients resilient (treat IDs as opaque; handle missing artifacts)

---

## Versioning

Current C‑Trees schema version:
- `CTREE_SCHEMA_VERSION = "0.1"`

Artifacts that carry a schema version:
- `.breadboard/ctrees/meta/ctree_events.jsonl` header line
- `.breadboard/ctrees/meta/ctree_snapshot.json`
- compiler/collapse payloads surfaced in `ctree_snapshot`

---

## Compatibility rules (clients)

Clients (TUI) should:
- treat `node_id`/`id` as **opaque strings**
- not assume any derivation from digests
- tolerate unknown keys (both in events and in tree-view `meta`)

---

## Notable changes so far (within `0.1`)

### Node ID derivation

Node IDs are required to be:
- deterministic across replays given identical event order
- unique even when identical payloads repeat

Earlier iterations derived `id` from `digest[:12]`. This can collide when the same payload repeats.

Current behavior:
- `digest` remains the semantic digest of `{kind,turn,payload}`
- `id` is derived from `(ordinal, digest_prefix)` to guarantee uniqueness

If you have an older `ctree_events.jsonl` where node ids collide:
- prefer regenerating artifacts (see “Regeneration” below)

### `task_event` determinism

To keep multi-agent/subagent recording replay-stable:
- `task_event.task_id` and `parent_task_id` are persisted as **stable aliases**
- runtime job/session ids are intentionally omitted from persisted C‑Trees

---

## Regeneration / backfill (recommended)

C‑Trees artifacts are intended as **derived** artifacts. When schema details change, the recommended migration is:

1) delete/recreate `.breadboard/ctrees/` for that workspace/session, or write into a fresh directory
2) backfill from a session eventlog containing `ctree_node` events:

```bash
python scripts/backfill_ctrees_from_eventlog.py --eventlog /path/to/events.jsonl --out /path/to/workspace/.breadboard/ctrees --overwrite
```

If you do not have a suitable eventlog, rerun the session to regenerate the artifacts.

Backfill metadata:
- Backfilled snapshots include `backfilled_from_eventlog: true` and `backfill_eventlog_path`.
- Parity-sensitive checks should treat backfilled artifacts as **non-authoritative** unless explicitly overridden.

Migration scaffold:
- `agentic_coder_prototype/ctrees/migrations.py` provides a placeholder migration API.
