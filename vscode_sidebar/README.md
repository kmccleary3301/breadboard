# BreadBoard VSCode Sidebar (V1 Scaffold)

This package is the initial VSCode/Cursor sidebar scaffold for BreadBoard.

Current status:

- Host controller created.
- Basic engine health connectivity check wired.
- Sidebar view + command contributions wired.
- Session/streaming/reducer UI not yet implemented.

## Development

```bash
cd breadboard_repo/vscode_sidebar
npm install
npm run typecheck
npm run build
```

Then open VSCode extension development host against this package.

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

This scaffold intentionally uses BreadBoard's existing HTTP + SSE bridge model.
It does not depend on editor-specific LM APIs.
