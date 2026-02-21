# BreadBoard Webapp (P0 Scaffold)

This package is the initial browser client for BreadBoard.

Current scope:

- Engine health check (`GET /health`)
- Session list/create
- Session attach + SSE event streaming (`GET /sessions/{id}/events`)
- Resume with `Last-Event-ID` and explicit 409 handling
- Input send and stop command
- Session file browser + snippet preview (`GET /sessions/{id}/files`)
- Artifact download (`GET /sessions/{id}/download?artifact=...`)
- stream-mdx transcript rendering (`StreamingMarkdown`)
- Deterministic local projection into transcript/tool/raw-event panes
- IndexedDB event cache for reconnect/reload hydration

## Development

```bash
cd breadboard_repo/bb_webapp
npm install
npm run dev
```

The app uses the local TypeScript SDK at `../sdk/ts` (`npm run sync:sdk` is wired into build/dev scripts).
The stream-mdx hosted worker is synced automatically to `public/workers/markdown-worker.js`.

Default engine base URL: `http://127.0.0.1:9099`
