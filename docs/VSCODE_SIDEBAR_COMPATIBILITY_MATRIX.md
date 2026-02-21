# VSCode Sidebar Compatibility Matrix (V1)

Last updated: `2026-02-21`

## Scope

This matrix covers the `vscode_sidebar` extension package and BreadBoard CLI-bridge transport compatibility.

## Supported Editor Versions

| Surface | Version band | Status | Notes |
|---|---:|---|---|
| VS Code | `>=1.95` | Supported | Matches extension `engines.vscode` requirement. |
| Cursor | Current builds based on VS Code `1.95+` | Supported (smoke) | Uses same extension host + webview APIs. |

## Supported BreadBoard Engine Surfaces

| API | Status | Notes |
|---|---|---|
| `GET /health` | Required | Connection probe. |
| `GET /sessions` | Required | Session selector + refresh. |
| `POST /sessions` | Required | New session command path. |
| `DELETE /sessions/{id}` | Required | Session delete flow. |
| `POST /sessions/{id}/input` | Required | Composer send flow. |
| `POST /sessions/{id}/command` | Required | Stop + permission decisions. |
| `GET /sessions/{id}/events` (SSE) | Required | Stream + replay resume (`Last-Event-ID`). |
| `GET /sessions/{id}/files` | Required | Files tab list + snippet preview. |
| `GET /sessions/{id}/download` | Optional but recommended | Artifact-backed diff open path. |

## Known Compatibility Caveats

1. Sidebar diff opening is optimized for text artifacts; binary artifacts are not rendered inline.
2. Workspace trust is required for mutating actions (`sendMessage`, `stopSession`, `deleteSession`, `approvePermission`).
3. If engine replay window is exceeded, stream continuity gap is surfaced and requires operator re-attach/recovery action.
