# VSCode Sidebar Troubleshooting (V1)

Last updated: `2026-02-21`

## Connection Failures

Symptom:
- Sidebar shows `Engine connection failed`.

Checks:
1. Confirm engine is running:
   - `curl -sS http://127.0.0.1:9099/health`
2. Confirm `breadboardSidebar.engineBaseUrl` matches runtime URL.
3. Confirm token is set (`BreadBoard: Set Engine Token`) when auth is enabled.

## SSE Stream Errors / Reconnect Loop

Symptom:
- Status alternates between connecting/error.

Checks:
1. Verify engine remains reachable during stream.
2. Check logs for network interruptions.
3. Re-attach session from sidebar (`Attach`).

## Continuity Gap Detected

Symptom:
- Status includes continuity gap warning.

Meaning:
- `Last-Event-ID` replay token is outside engine replay window.

Recovery:
1. Re-attach to active session.
2. If needed, stop and restart session stream.
3. Use Files tab + artifacts to recover missing context where applicable.

## Permission Actions Fail

Symptom:
- Approve/deny buttons fail with workspace trust or bad request.

Checks:
1. Ensure workspace is trusted in editor.
2. Ensure request card contains valid `request_id`.
3. Retry with `allow_once` first to isolate rule persistence issues.

## Files Tab Errors

Symptom:
- File list/snippet request fails.

Checks:
1. Ensure session is active and attached.
2. Ensure path is workspace-relative.
3. Retry path with `.` then drill down.

## Diff Open Issues

Symptom:
- `Open Diff` fails.

Checks:
1. Ensure selected item is a file (not directory).
2. Confirm `filePath` exists in local workspace.
3. If artifact download fails, retry after fresh event emission/tool result.
