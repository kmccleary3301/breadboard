# Mock SSE Script Specification

This document explains how the CLI’s mock streaming service works so engineers can author deterministic SSE payloads without touching the real engine.

## Goals

- Provide deterministic “provider” responses when the engine is offline or credentials are unavailable.
- Exercise guardrail, tool, and diff rendering paths inside the TUI/stress suites without depending on live traffic.
- Enable chaos/fuzz tests to specify jitter/drop behaviour via CLI flags instead of editing Python code.

## Script Format

Mock scripts live under `scripts/mock_sse_*.json` and are simple arrays of steps:

```json
[
  {
    "delayMs": 250,
    "event": {
      "type": "assistant_message",
      "payload": {
        "text": "Hello from the mock provider",
        "message": {
          "role": "assistant",
          "content": "Hello from the mock provider"
        }
      }
    }
  }
]
```

Each entry contains:

- `delayMs` *(optional)* – milliseconds to wait before emitting the event (defaults to `0`).
- `event` – the JSON payload that mimics the FastAPI `/events` stream (`type`, `turn`, `payload`, etc.). Use the same shape returned by the real engine (see `src/api/types.ts`).

Scripts are looped when `--mock-sse-loop` is set; otherwise the server emits the sequence once and closes the stream.

## CLI Integration

`scripts/run_stress_bundles.ts` exposes the following flags:

| Flag | Purpose |
| --- | --- |
| `--mock-sse-script <path>` | Use the specified script for **all** cases. |
| `--mock-sse-host <ip>` / `--mock-sse-port <port>` | Bind address for the Node mock server (defaults `127.0.0.1:9191`). |
| `--mock-sse-loop` | Loop the script so multiple `/input` calls reuse the same payload. |
| `--mock-sse-delay-multiplier <x>` | Scale every `delayMs` value (e.g., `0.5` to speed up, `2` to slow down). |
| `--mock-sse-jitter-ms <ms>` | Add random jitter per event. |
| `--mock-sse-drop-rate <0-1>` | Drop events with the given probability. |
| `--case-mock-sse` | Respect each case’s `mockSseScript` field (preferred—see below). |

`stress:ci` sets `--case-mock-sse` by default so scenarios with `mockSseScript` automatically attach the mock server. Individual cases can still override the script via `--mock-sse-script` for ad-hoc experiments.

### Case-Level Scripts

Add `mockSseScript: "scripts/mock_sse_guardrail.json"` to a case inside `STRESS_CASES` to force that scenario to run against the mock when `--case-mock-sse` is enabled. This keeps the happy-path cases on the real FastAPI bridge while allowing specific runs (e.g., guardrail regression tests) to be deterministic.

## Provided Fixtures

| Script | Description |
| --- | --- |
| `mock_sse_sample.json` | Simple “hello world” run used by the `mock_hello` scenario. |
| `mock_sse_guardrail.json` | Emits a guardrail interruption so the banner/telemetry path can be validated without touching providers. |
| `mock_sse_diff.json` | Emits tool-call/diff events so the PTY suite can exercise diff rendering deterministically. |

Copy one of these files to bootstrap a new mock sequence; keep the JSON small so diffs remain readable.

## Open Questions

1. **Multi-turn sessions:** today scripts cover a single `/input` → completion cycle. We may need a richer mini-language to describe multi-turn conversations (multiple `turn` values, interleaved tool calls).
2. **Attachments / binary payloads:** the mock server currently rejects `/attachments`; if attachment replay becomes necessary we should extend the server to store blobs under the session dir.
3. **Text vs. JSON events:** real providers sometimes stream plain text before emitting JSON events (e.g., SSE `data: foo`). If we need to mimic that behaviour we might want to add a `kind: "text" | "json"` discriminator to each script step.
4. **Chaos metadata schema:** the `timeline_summary.json` currently stores simple knob values (`delayMultiplier`, `dropRate`, etc.). We may want a versioned schema if we add more complex fault injection later.

Future contributors should update this document whenever new script fields or behaviours are introduced.
