# BreadBoard Webapp (P0/P1/P2 Incremental Surface)

This package is the browser operator surface for BreadBoard sessions.

## Implemented Surface

- Session list/create/attach/stop.
- SSE stream with `Last-Event-ID`, replay catch-up, heartbeat timeout retry, and 409 resume-window handling.
- Deterministic event projection with memory bounds and IndexedDB compaction.
- Snapshot + tail replay hydration on attach.
- Transcript rendering via `stream-mdx` with stable-prefix handling and unsafe-content plaintext fallback.
- Tool/event projection with diff detection and interactive diff viewer (unified/side-by-side, hunk copy, open file path).
- Checkpoint list + restore controls with reconnect-safe restore flow.
- Permission queue decisions + permission ledger filters + revoke attempts (with fallback behavior if unsupported).
- Files browser/snippet preview and artifact download.
- Local transcript/tool/artifact search with jump-to-result navigation.
- Task/subagent tree projection (`task_event`, `ctree_node`, `ctree_snapshot`) with status rollup and event jumps.
- Replay package export/import with deterministic ordering and projection hash display.
- Client telemetry counters (queue depth/latency, flush counts, stale drops) and audit log panel.
- Connection mode strategy (`local`/`sandbox`/`remote`) with mode-scoped token policy.

## Security & Trust Notes

- Markdown/assistant content is treated as untrusted; unsafe patterns degrade to plaintext rendering.
- Replay export and raw event debug views redact sensitive keys.
- Remote mode only attaches Authorization headers; local/sandbox modes do not by default.
- A baseline CSP is set in `index.html`.

## Development

```bash
cd breadboard_repo/bb_webapp
npm install
npm run dev
```

The app uses the local TypeScript SDK at `../sdk/ts` (`sync:sdk` is built into dev/build/typecheck).
The stream worker is synced to `public/workers/markdown-worker.js`.

## Quality Gates

- Full baseline gate:

```bash
npm run gate:p0
```

Includes:
- full vitest suite,
- typecheck/build,
- dist CSP verification (`verify:csp-dist`).

- P1/P2 hardening gate (replay determinism + diff/task/checkpoint/parity suites):

```bash
npm run gate:p1p2
```

Includes:
- backend command inventory parity check (`check:backend-commands`),
- targeted P1/P2 vitest suite,
- typecheck/build,
- dist CSP verification.

- Combined gate:

```bash
npm run gate:full
```

## Data Retention and Cache Policy

- Event cache is compacted per session (`maxEventsPerSession=2000`).
- Projection memory bounds are enforced for transcript/tools/events/permissions/task nodes.
- Projection snapshots are written periodically and replayed with tail events on attach.

## Operator Workflows

- Stream recovery: use `Recover Stream` when state enters `gap`.
- Checkpoint restore: refresh list, select checkpoint, restore, and wait for automatic reattach.
- Permission governance: resolve pending requests, inspect ledger, re-apply rules, and attempt revokes.
- Diff/file triage: open tool diffs and jump directly into file preview.
- Diagnostics: run health/status/model checks from the diagnostics button.

## Additional Docs

- `bb_webapp/RUNBOOK.md` for remote-safe setup and troubleshooting.
- `bb_webapp/RELEASE_CHECKLIST.md` for release verification steps.
