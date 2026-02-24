# Webapp E2E QC Matrix

This matrix defines the minimum QC surface for operator-facing regressions.

## Dimensions

- Platforms:
  - `desktop-chromium` (1366x920)
  - `mobile-chromium` (Pixel 7 profile)
- Connection modes:
  - `local`
  - `remote`
- Stream states:
  - `idle`
  - `streaming`
  - `gap` + recovery
- Failure classes:
  - unreachable endpoint (`Failed to fetch`)
  - auth failure (`HTTP 401`)

## Scenario Coverage

- `shell renders with deterministic diagnostics status`
- `replay import hydrates transcript, tools, permissions, checkpoints, and task tree`
- `connection mode and token policy persist across reload`
- `live workflow: create attach send permissions checkpoints files and artifacts`
- `gap workflow: sequence gap surfaces recover flow and returns to active stream`
- `remote mode invalid base url surfaces validation error state`
- `remote mode auth failure surfaces 401 and recovers after token update`

## Visual QC Targets

- `shell-connection-pill.png`
- `replay-import-tools-panel.png`
- `replay-import-task-tree-panel.png`
- `connection-mode-pill.png`
- `live-workflow-permissions-pending-panel.png`
- `live-workflow-tools-panel.png`
- `gap-state-pill.png`
- `gap-recovered-pill.png`

## Commands

```bash
npm run e2e:spec
npm run e2e:ci
npm run e2e:validate
```

For intentional visual updates:

```bash
npm run e2e:spec -- --update-snapshots
```
