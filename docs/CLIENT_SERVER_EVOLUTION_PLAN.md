# Client/Server Evolution Plan (Draft)

This plan folds the OpenCode client/server analysis into BreadBoard’s current roadmap. It assumes **no Python bundling** and a **Python + Ray engine** that is always run as a normal service process.

## Non‑Negotiables
- Engine remains **Python** (FastAPI + Ray); no bundled executables.
- Client/server boundary stays **HTTP + SSE** (optionally WebSocket later).
- Protocol versioning remains explicit and enforced.
- No large destructive changes during rollout; isolate new systems behind flags.

## Current State (Baseline)
- CLI/TUI (Node) talks to engine over HTTP + SSE.
- Engine provides `/health`, `/status`, and session endpoints.
- Protocol version is explicit; health returns version fields.
- Sessions and replay buffers are **in‑memory** (not durable).

## Layered Additions (Aligned to OpenCode Lessons)

### Layer 0 — Constraints & Plan Hardening (Immediate)
Deliverables:
- This plan and explicit “no bundling” constraint recorded in docs.
- Clear ownership boundaries (engine vs TUI).

### Layer 1 — Contract Hygiene & Transport Consistency
Deliverables:
- Single client abstraction for local/remote engine (no protocol forks).
- Compatibility handshake: protocol version + engine version are required fields (already present).
- Explicit strict/soft protocol mode in client config (warn vs fail).

### Layer 2 — Durable Persistence (Highest Priority)
Deliverables:
- Session metadata stored on disk (SQLite or JSON).
- **Append‑only JSONL event log per session**; replay reads from disk.
- In‑memory replay buffers become a cache, not the source of truth.
- Engine restart preserves sessions and allows reattach.
Notes:
- A best-effort JSONL event log can be enabled via `BREADBOARD_EVENTLOG_DIR` (current scaffold).
- Optional bootstrap + replay flags exist for dev (`BREADBOARD_EVENTLOG_BOOTSTRAP`, `BREADBOARD_EVENTLOG_REPLAY`).
- A minimal JSON session index can be enabled via `BREADBOARD_SESSION_INDEX`.
See: `docs/SESSION_PERSISTENCE_STRATEGY.md`

### Layer 3 — Engine Install (No Bundling)
Deliverables:
- Engine published as PyPI package (`breadboard-engine`).
- CLI installs engine into a **managed venv** (pinned version).
- CLI commands: `engine install`, `engine upgrade`, `engine doctor`.

### Layer 4 — CLI Shipping UX (OpenCode‑like)
Deliverables:
- CLI distributed as native binaries via curl + npm wrapper.
- `breadboard upgrade` updates both CLI and engine venv.
- Logs are routed to files when in interactive UI mode (no stdout pollution).

### Layer 5 — Operational Hardening
Deliverables:
- Crash recovery prompts in UI with last‑log tail.
- Engine lockfile with pid/port/token for safe restarts.
- Optional TLS guidance for remote binds.

## Persistence Model (Layer 2 Detail)
- Sessions table: id, status, created_at, updated_at, config_hash, protocol_version.
- Event log: `~/.breadboard/data/sessions/<id>/events.jsonl`
  - Each line is the same payload emitted over SSE, with seq preserved.
- Optional snapshot files for faster rehydrate.

## Risks & Guardrails
- Avoid schema changes without parity bump and golden regen.
- Do not send engine logs to stdout when TUI is active.
- Ray tasks must emit deterministic ordering metadata (turn id + step id).

## Acceptance Criteria (Minimal)
- Engine restart preserves session list and enables replay to latest state.
- CLI can bootstrap engine venv and start server without manual Python setup.
- Protocol mismatch triggers a clear warning or fail (configurable).

## Status
Draft — intended to guide incremental implementation with minimal disruption.
