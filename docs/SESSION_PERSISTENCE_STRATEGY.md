# Session Persistence Strategy (Draft)

This document describes the **durable session persistence** approach for BreadBoard’s engine.

## Goals
- Preserve sessions across engine restarts.
- Provide deterministic replay for parity and goldens.
- Keep persistence opt‑in while the system matures.

## Components

### 1) JSONL Event Log (per session)
- Path: `~/.breadboard/data/sessions/<session_id>/events.jsonl`
- Content: one JSON object per line, matching the SSE payload (`SessionEvent.asdict`).
- Enabled by: `BREADBOARD_EVENTLOG_DIR`
- Optional bootstrap: `BREADBOARD_EVENTLOG_BOOTSTRAP=1`
- Optional replay hydration: `BREADBOARD_EVENTLOG_REPLAY=1`
- Optional cap: `BREADBOARD_EVENTLOG_MAX_MB`

### 2) Session Index (metadata)
Tracks `SessionSummary` entries for discovery and UI list screens.

- JSON index (default): `index.json`
- SQLite index (optional): `sessions.sqlite`
- Enabled by: `BREADBOARD_SESSION_INDEX=1`
- Engine selection: `BREADBOARD_SESSION_INDEX_ENGINE=json|sqlite`

## Current Status
- JSONL log and JSON index are implemented and gated by env flags.
- SQLite index exists as an optional backend.
- Replay hydration is **opt‑in** and limited to a bounded in‑memory window.

## Open Questions
- Finalize SQLite schema migrations.
- Decide on rotation/archival for long sessions.
- Decide how to store large artifacts (separate artifact store).

## Status
Draft — implementation is gated and non‑default.
