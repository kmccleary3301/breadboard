# VSCode Sidebar Feature Status (V1)

Last updated: `2026-02-21`

## Implemented

### Transport and Session

- [x] Health check and connection state.
- [x] Session list/create/delete/attach.
- [x] Input send and stop command.
- [x] SSE stream ingest with reconnection.
- [x] Per-session `Last-Event-ID` persistence and resume.
- [x] Host event batching to webview.

### Contract and Validation

- [x] RPC request envelope parsing (`v1`).
- [x] Method param parsers for key methods.
- [x] Host->webview payload sanitization (`connection/state/events`).
- [x] Unknown method logging path.

### UX

- [x] Tabbed panes: Chat, Tasks, Files, Run.
- [x] Structured transcript cards.
- [x] Deterministic render throttle.
- [x] Composer keyboard rules (`Enter` send, `Shift+Enter` newline).
- [x] Auto-scroll lock (follow tail unless user scrolls away).
- [x] Permission request actions (allow once / deny / allow rule).
- [x] Files list + snippet preview + `@path` insert.
- [x] Open diff action from transcript/files.

### Tests

- [x] Transcript reducer unit tests.
- [x] RPC envelope parser unit tests.
- [x] RPC param parser unit tests.
- [x] Host->webview payload sanitization unit tests.

## Planned (Not Yet Implemented)

- [ ] Integration tests for forced disconnect/reconnect/resume.
- [ ] Integration tests for permission request/response end-to-end rendering.
- [ ] Deterministic golden tests for full UI projection state.
- [ ] Cursor smoke lane automation in CI.
- [ ] Artifact panel and richer run diagnostics.
