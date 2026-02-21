# VSCode Sidebar Quickstart (V1)

Last updated: `2026-02-21`

## Prerequisites

1. BreadBoard engine/CLI bridge running locally (default: `http://127.0.0.1:9099`).
2. A valid engine bearer token (if token auth is enabled).
3. VS Code or Cursor with extension development host.

## Build and Typecheck

```bash
cd breadboard_repo/vscode_sidebar
npm install
npm run typecheck
npm run test
```

## Launch Extension Host

1. Open `breadboard_repo/vscode_sidebar` in VS Code.
2. Run `F5` (Run Extension).
3. In the extension host window, open the BreadBoard activity bar view.

## First Session Flow

1. Run command: `BreadBoard: Set Engine Token` (if required).
2. Run command: `BreadBoard: Check Engine Connection`.
3. Run command: `BreadBoard: New Session`.
4. In sidebar:
   1. click `Refresh sessions`,
   2. select a session,
   3. click `Attach`.
5. Send a prompt from composer (`Enter` to send, `Shift+Enter` newline).
6. Use `Stop` to request stop for active run.

## Tabs

1. `Chat`:
   - Structured transcript cards for assistant/user/tool/permission/task events.
   - Permission actions on request cards.
2. `Tasks`:
   - Task event stream projection.
3. `Files`:
   - Workspace file listing, snippet preview, insert `@path`, open diff.
4. `Run`:
   - Active session, event totals, pending permission count, run state.

## Settings

Extension settings:

- `breadboardSidebar.engineBaseUrl` (default `http://127.0.0.1:9099`)
- `breadboardSidebar.defaultConfigPath` (default `agent_configs/base_v2.yaml`)
