# VSCode Sidebar Release Packaging (V1)

Last updated: `2026-02-21`

## Versioning Policy

1. Extension version follows semver.
2. Patch release: bugfix only, no contract shape breaks.
3. Minor release: additive RPC/event fields allowed under `v1`.
4. Major release: host/webview contract break, or required API behavior break.

## Reproducible Build Steps

From `breadboard_repo/vscode_sidebar`:

```bash
npm ci
npm run typecheck
npm run test
npm run smoke:cursor
npm run smoke:live-engine
```

Optional package step (requires `vsce`):

```bash
npx @vscode/vsce package --out ../../docs_tmp/interfaces/vscode_sidebar_release_artifacts/breadboard-sidebar-0.0.1.vsix
sha256sum ../../docs_tmp/interfaces/vscode_sidebar_release_artifacts/breadboard-sidebar-0.0.1.vsix > ../../docs_tmp/interfaces/vscode_sidebar_release_artifacts/breadboard-sidebar-0.0.1.vsix.sha256
```

Packaging status:

- `vsce package` now runs cleanly (no repository/license warnings).

## Artifact Naming

Recommended:

`breadboard-sidebar-v<version>-<gitsha>.vsix`

Checksum file:

`breadboard-sidebar-v<version>-<gitsha>.sha256`

## Release Checklist

- [x] Typecheck/tests/smoke green
- [x] Compatibility matrix updated
- [x] Feature status updated
- [x] Troubleshooting doc updated
- [x] Known limits reviewed
- [x] Alpha rollout gate reviewed
- [x] VSIX packaged (if publishing extension artifact)
- [x] Checksum captured and attached to release notes
