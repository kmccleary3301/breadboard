# BreadBoard Webapp (P0 Scaffold)

This package is the initial browser client for BreadBoard.

Current scope:

- Engine health check (`GET /health`)
- Session list/create
- Session attach + SSE event streaming (`GET /sessions/{id}/events`)
- Resume with `Last-Event-ID`, replay catch-up, and explicit 409 gap handling
- Input send and stop command
- Permission queue + decisions (`permission_decision`: allow/deny once/always + deny-stop)
- Session file browser + snippet preview (`GET /sessions/{id}/files`)
- Artifact download (`GET /sessions/{id}/download?artifact=...`)
- stream-mdx transcript rendering (`StreamingMarkdown`)
- Deterministic local projection into transcript/tool/raw-event panes
- IndexedDB event cache with bounded compaction + schema migration guard
- Projection snapshot + tail replay hydration on session attach
- Debug replay package export/import (deterministic ordering + validation)
- Coalesced delta rendering pipeline with queue/latency metrics
- Projection hash display and replay determinism tests
- Markdown security fail-closed path for unsafe HTML/script/link patterns

## Development

```bash
cd breadboard_repo/bb_webapp
npm install
npm run dev
```

The app uses the local TypeScript SDK at `../sdk/ts` (`npm run sync:sdk` is wired into build/dev scripts).
The stream-mdx hosted worker is synced automatically to `public/workers/markdown-worker.js`.

Default engine base URL: `http://127.0.0.1:9099`

## Quality Gates

Run all P0 checks:

```bash
npm run gate:p0
```

This runs:
- `npm run test`
- `npm run typecheck`
- `npm run build`

## Operator Notes

- If streaming enters `gap`, use `Recover Stream` to clear local cursor and re-attach.
- `Export Replay` writes a validated JSON replay package for the active session.
- `Import Replay` hydrates local projection/cache from a replay package (for diagnostics/review).
- Raw events panel includes projection hash and client queue telemetry to debug stream health.
