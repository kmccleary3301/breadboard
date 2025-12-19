# Node CLI Progress — Phase 1

## Implemented
- Added configuration loader (`src/config/appConfig.ts`) with `.env` support, remote streaming default toggles, and session cache path resolution.
- Built REST client + SSE generator (`src/api/client.ts`, `src/api/stream.ts`) enabling commands to call `/sessions` and consume `/sessions/{id}/events`.
- Introduced persistent session cache (`src/cache/sessionCache.ts`) and wired `kyle sessions` to merge backend state with local history.
- Implemented `kyle ask` (streaming stdout), `kyle sessions`, `kyle repl` (prototype Ink TUI), and `kyle resume` commands, each leveraging the new backend façade.
- Added Vitest-based regression for session cache behavior (`tests/sessionCache.test.ts`).

## Next Targets
- Flesh out `repl` UI (conversation panes, slash command routing) and integrate `/sessions/{id}/input` interactions.
- Surface file/artifact commands (`kyle files`, `kyle artifacts`) and permission-mode toggles.
- Expand automated coverage: mocked-backend command tests, streaming replay scenarios.
- Document CLI usage and add quick-start guide (`docs/cli_phase_1/` updates pending).

## Operational Notes
- Remote streaming remains opt-in via `KYLECODE_ENABLE_REMOTE_STREAM=1` or command flag `--remote-stream`.
- Cache writes occur on every session list/start/resume; max history = 50 sessions under `~/.kyle/sessions.json` (overridable via `KYLECODE_SESSION_CACHE`).
- CLI expects backend at `KYLECODE_API_URL` (default `http://127.0.0.1:9099`); add `KYLECODE_API_TOKEN` if auth is enabled.
