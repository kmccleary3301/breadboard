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
```

Optional package step (requires `vsce`):

```bash
npx @vscode/vsce package --out dist/breadboard-sidebar.vsix
shasum -a 256 dist/breadboard-sidebar.vsix
```

## Artifact Naming

Recommended:

`breadboard-sidebar-v<version>-<gitsha>.vsix`

Checksum file:

`breadboard-sidebar-v<version>-<gitsha>.sha256`

## Release Checklist

- [ ] Typecheck/tests/smoke green
- [ ] Compatibility matrix updated
- [ ] Feature status updated
- [ ] Troubleshooting doc updated
- [ ] Known limits reviewed
- [ ] Alpha rollout gate reviewed
- [ ] VSIX packaged (if publishing extension artifact)
- [ ] Checksum captured and attached to release notes
