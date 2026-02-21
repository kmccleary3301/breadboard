# BreadBoard VSCode Sidebar (V1)

This package is the initial VSCode/Cursor sidebar scaffold for BreadBoard.

Current status:

- Host controller wired to BreadBoard engine HTTP + SSE session APIs.
- Sidebar view + command contributions wired.
- Session create/list/attach/send/stop/delete flows implemented.
- SSE resume (`Last-Event-ID`) with reconnect and continuity-gap detection implemented.
- Deterministic transcript reducer + bounded tail preview implemented.
- Tabbed operator UI (`Chat`, `Tasks`, `Files`, `Run`) implemented.
- Structured cards for tool/permission/task events implemented.
- Reducer unit tests implemented.

## Development

```bash
cd breadboard_repo/vscode_sidebar
npm install
npm run typecheck
npm run build
npm run test
```

Then open VSCode extension development host against this package.

## V1 Docs

- `docs/VSCODE_SIDEBAR_QUICKSTART.md`
- `docs/VSCODE_SIDEBAR_COMPATIBILITY_MATRIX.md`
- `docs/VSCODE_SIDEBAR_TROUBLESHOOTING.md`
- `docs/VSCODE_SIDEBAR_FEATURE_STATUS.md`
- `docs/VSCODE_SIDEBAR_DOCS_INDEX.md`

## Settings

- `breadboardSidebar.engineBaseUrl` (default `http://127.0.0.1:9099`)
- `breadboardSidebar.defaultConfigPath` (default `agent_configs/base_v2.yaml`)

Commands:

- `BreadBoard: Check Engine Connection`
- `BreadBoard: Set Engine Token`
- `BreadBoard: Clear Engine Token`
- `BreadBoard: New Session`
- `BreadBoard: Attach to Session Stream`
- `BreadBoard: Send Message to Active Session`
- `BreadBoard: Stop Active Session`
- `BreadBoard: Delete Session`

## Notes

This extension intentionally uses BreadBoard's existing HTTP + SSE bridge model.
It does not depend on editor-specific LM APIs.
