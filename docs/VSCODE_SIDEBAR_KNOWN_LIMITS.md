# VSCode Sidebar Known Limits (V1)

Last updated: `2026-02-21`

| Area | Current Limit | Impact | Mitigation |
|---|---|---|---|
| Integration coverage | Host integration tests use mocked transport/runtime | Real editor edge cases may still exist | Run alpha rollout checklist before beta |
| Continuity rebuild | Gap detection implemented, but artifact-backed continuity rebuild is limited | Operator may need manual re-attach/replay | Use troubleshooting recovery flow |
| Diff rendering | Diff action depends on artifact/file availability | Some tool outputs may not produce openable diffs | Use Files tab snippet and workspace file open |
| Webview testability | UI logic largely embedded in webview script | Fine-grained UI unit tests are limited | Continue extracting logic into testable modules |
| Cursor CI | Local smoke lane exists; full hosted Cursor lane not yet automated | Regressions may be discovered later | Keep local `smoke:cursor` mandatory in release checklist |
