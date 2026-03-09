# OpenClaw Host Bridge V1

This document records the first concrete BreadBoard host-bridge seam for OpenClaw's embedded runner.

## Boundary

Primary host seam:

- `runEmbeddedPiAgent(...)`

The current BreadBoard bridge does **not** attempt full Pi replacement. It only supports a deliberately narrow embedded-run slice and preserves native fallback for unsupported runs.

## Supported slice

The current bridge accepts:

- `sessionId`
- `sessionKey`
- `sessionFile`
- `workspaceDir`
- `prompt`
- `provider`
- `model`
- `authProfileId`
- `authProfileIdSource`
- `thinkLevel`
- `reasoningLevel`
- `timeoutMs`
- `runId`
- callback surfaces for:
  - assistant start
  - partial reply
  - reasoning deltas/end
  - tool results
  - generic agent events

## Explicitly unsupported in V1

These currently force native fallback or an unsupported-slice error:

- multimodal `images`
- `clientTools`
- `disableTools`
- block-reply callback surfaces
- richer channel / group routing fields
- inherited subagent policy surfaces

## Bridge semantics

The bridge:

1. maps OpenClaw embedded-run params into `bb.run_request.v1`
2. requires an injected BreadBoard executor for the supported slice
3. projects BreadBoard output into:
   - OpenClaw host callbacks
   - a minimal `EmbeddedPiRunResult`-compatible result
4. preserves an explicit native fallback seam

## Why this matters

This is the first proving-ground bridge that exercises a real host boundary against the stronger kernel contract program. It is intentionally narrow and should be treated as an executable seam proof, not as full OpenClaw compatibility.
