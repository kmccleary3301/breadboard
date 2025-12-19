# Phase 1 Backend Streaming Delta

## Overview
- Added optional event-emitter plumbing inside `SessionState` and `OpenAIConductor.run_agentic_loop` so the engine produces normalized `turn_start`, `assistant_message`, `tool_call`, `tool_result`, and `completion` events without modifying existing reasoning flows.
- `AgenticCoder.run_task` now accepts `stream` and `event_queue` arguments. Local sessions use direct callbacks; Ray-backed sessions enqueue events into `ray.util.queue.Queue` when the CLI backend requests remote streaming.
- The CLI backend’s `SessionRunner` consumes both local emitters and queue-based payloads, translating them into SSE-friendly payloads while retaining fallback logic that publishes the final assistant message when providers disable deltas mid-run.

## Feature Toggles
- Remote streaming is disabled by default. Enable via environment variable `KYLECODE_ENABLE_REMOTE_STREAM=1` or per-session metadata flag `{"enable_remote_stream": true}`.
- Local mode always streams because the conductor runs in-process; no toggle required.

## Instrumentation
- `SessionRunner` logs the requested streaming mode, whether the remote queue is active, and the number of published events when the session drains. Queue capacity issues produce throttled warnings.
- Completion events now terminate the SSE generator once a sentinel (`None`) is observed, preventing hanging clients after runs finish.

## Testing Additions
- `tests/test_session_state_events.py` covers message deduping, tool telemetry emission, queue pumping, and async event-stream helpers. Tests automatically skip FastAPI-dependent cases if the package is absent.
- `tests/test_cli_backend_streaming.py` exercises the FastAPI `/sessions/{id}/events` endpoint end-to-end with `httpx.ASGITransport`, verifying ordered delivery and completion shutdown semantics. Tests run inside the `ray_sce_test` conda env.
- All streaming tests are now part of `conda run -n ray_sce_test pytest tests/test_cli_backend_streaming.py tests/test_session_state_events.py`.

## Integration Notes
- Engine teams adding new event types should emit them via `SessionState._emit_event` to automatically surface in the CLI; remember to extend the mapping in `SessionRunner._translate_runtime_event`.
- Remote conductor changes must preserve queue compatibility—non-serializable payloads will break the CLI stream. Favor plain dicts/lists/strings.
- When rebasing runtime changes, keep the sentinel `None` contract intact so SSE clients terminate promptly. The backend drains any residual queue entries at session wrap-up.
