# Engine Persistence Config (Draft)

This doc lists environment variables that control **engine-side persistence**.

## Event Log (JSONL)

- `BREADBOARD_EVENTLOG_DIR=...`  
  Enables per-session JSONL event logs under the given directory.

- `BREADBOARD_EVENTLOG_CANONICAL_ONLY=1`  
  Persist only canonical v2 events to disk (legacy events remain stream-only).

- `BREADBOARD_EVENTLOG_BOOTSTRAP=1`  
  On engine startup, preload session summaries from existing event logs.

- `BREADBOARD_EVENTLOG_REPLAY=1`  
  When bootstrapping, also hydrate the in-memory replay buffer (bounded).

- `BREADBOARD_EVENTLOG_MAX_MB=N`  
  Soft cap for event log growth. If set and exceeded, new log appends are skipped.

## Session JSONL Export (Draft)

- `BREADBOARD_SESSION_JSONL_EXPORT=1`  
  Export a `session.jsonl` alongside each `events.jsonl` after the run finishes.

- `BREADBOARD_SESSION_JSONL_OVERWRITE=1`  
  Overwrite an existing `session.jsonl` when exporting.

## Session JSONL Live Persistence (Draft)

- `BREADBOARD_SESSION_JSONL_PERSIST=1`  
  Append session entries to `session.jsonl` as events stream in.

- `BREADBOARD_SESSION_JSONL_DIR=...`  
  Root directory for live `session.jsonl` files (defaults to `BREADBOARD_EVENTLOG_DIR`).

- `BREADBOARD_SESSION_JSONL_RESUME=1`  
  If the session file already exists, resume from the last entry (default: on).

## Session Index

- `BREADBOARD_SESSION_INDEX=1`  
  Enable session summary persistence.

- `BREADBOARD_SESSION_INDEX_DIR=...`  
  Override the index root directory (defaults to event log dir).

- `BREADBOARD_SESSION_INDEX_ENGINE=json|sqlite`  
  Select storage backend (default: `json`).

## Defaults

All persistence features are **off by default** unless explicitly enabled.

## Session Compaction Summaries (Experimental)

- `BREADBOARD_SESSION_COMPACTION=1`  
  Emit `session.compaction` summaries automatically at turn boundaries.

- `BREADBOARD_SESSION_COMPACTION_MESSAGE_THRESHOLD=N`  
  Minimum message count before emitting summaries (default: 0).

- `BREADBOARD_SESSION_COMPACTION_TURN_INTERVAL=N`  
  Minimum turns between auto summaries (default: 0).

- `BREADBOARD_SESSION_COMPACTION_MAX_CHARS=N`  
  Maximum summary length (default: 240).

- `BREADBOARD_SESSION_COMPACTION_RECENT_LIMIT=N`  
  Number of recent messages to include (default: 4).

## Status
Draft — subject to change as persistence matures.
