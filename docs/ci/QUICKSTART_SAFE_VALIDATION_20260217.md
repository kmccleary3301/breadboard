# Quickstart Safe Validation (2026-02-17)

This record confirms the launch quickstart flow was revalidated in safe mode
against the restored repository state.

## Scope

- Repo: `breadboard_repo/`
- Config: `agent_configs/opencode_mock_c_fs.yaml`
- Safety mode: workspace preflight enforced before runtime commands.
- Validation style: local command execution, no destructive operations.

## Commands Executed

```bash
python scripts/preflight_workspace_safety.py --config agent_configs/opencode_mock_c_fs.yaml
npm -C tui_skeleton ci
npm -C tui_skeleton run build
breadboard doctor --config agent_configs/opencode_mock_c_fs.yaml
breadboard run --config agent_configs/opencode_mock_c_fs.yaml "Say hi and exit."
```

## Result Summary

- `preflight_workspace_safety.py`: PASS
- `npm ci`: PASS
- `npm run build`: PASS
- `breadboard doctor`: PASS
- `breadboard run`: PASS

Observed success details:

- The run completed with a valid completion envelope.
- Active workspace resolved to a non-root safe path:
  - `/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/agent_ws_opencode`

## Regression Found and Fixed During Validation

Before final pass, `breadboard run` was defaulting to `process.cwd()` when
`--workspace` was omitted, which could resolve to repo root and trigger safety
rejection.

Fix applied:

- `tui_skeleton/src/utils/paths.ts`
  - `resolveBreadboardWorkspace()` now returns `undefined` when no explicit
    workspace is provided, allowing engine config workspace defaults to apply.
- Added regression test:
  - `tui_skeleton/src/utils/__tests__/paths.test.ts`

Verification after fix:

```bash
npm -C tui_skeleton run test -- src/utils/__tests__/paths.test.ts
npm -C tui_skeleton run build
```

Both commands passed.

