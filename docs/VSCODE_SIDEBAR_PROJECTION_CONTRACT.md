# VSCode Sidebar Projection Contract (V1)

Last updated: `2026-02-23`

## Purpose

Define one canonical event-projection substrate for:

1. SDK event stream normalization (`@breadboard/sdk`).
2. VSCode sidebar host ingest (SSE -> event envelope).
3. VSCode sidebar webview sanitization/reduction.
4. BreadBoard webapp transcript/tool rendering.

This prevents drift between interfaces when event aliases, payload text surfaces, and timestamp/id fallbacks evolve.

## Canonical surfaces

1. Type alias normalization:
   1. `tool.result` -> `tool_result`
   2. `assistant.message.delta` -> `assistant_delta`
   3. `assistant.message.start` -> `assistant_message_start`
   4. `assistant.message.end` -> `assistant_message_end`
2. Payload text extraction:
   1. Prefer semantic text fields (`text`, `content`, `message`, `summary`) and delta/detail text.
3. Envelope normalization:
   1. Stable `id` selection (`id` / `event_id` / `eventId` / fallback).
   2. Session fallback propagation.
   3. Timestamp normalization to millisecond precision.

## Implemented wiring

1. SDK:
   1. `sdk/ts/src/projection.ts`
   2. Used by `sdk/ts/src/stream.ts`
2. Sidebar:
   1. `vscode_sidebar/src/projectionContract.ts`
   2. Used by:
      1. `vscode_sidebar/src/hostController.ts`
      2. `vscode_sidebar/src/rpcEvents.ts`
      3. `vscode_sidebar/src/transcriptReducer.ts`
3. Webapp:
   1. `bb_webapp/src/App.tsx` imports projection helpers from `@breadboard/sdk`.

## Current guarantees

1. The same alias normalization drives stream ingest and transcript reduction.
2. `assistant_message_end` and related normalized event forms are handled consistently in UI reducers.
3. Tool/result summaries and transcript text extraction use a shared extraction strategy.

## Validation

1. Sidebar test suite:
   1. `npm test` in `vscode_sidebar/`
2. New projection contract tests:
   1. `vscode_sidebar/src/__tests__/projectionContract.test.ts`
3. Webapp build gate:
   1. `npm run build` in `bb_webapp/`
4. Conformance regression gate:
   1. `bash scripts/run_wave_a_conformance_bundle.sh`

## Next hardening targets

1. Add fixture-based cross-interface parity lane:
   1. Feed one canonical event fixture set through SDK and sidebar normalizers.
   2. Assert equivalent normalized envelope/type/text outputs.
2. Add CI entry for projection parity lane so substrate drift is blocked pre-merge.
