# OpenClaw Host Bridge V1

This document records the first concrete BreadBoard host-bridge seam for OpenClaw's embedded runner.

It now also serves as the first concrete proving ground for the reusable Host Kit abstraction introduced in V3.

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
- provider-quirk preservation for the supported embedded lane:
  - provider family
  - runtime id
  - route id
  - response finish reason
- one narrow tool-bearing lane when:
  - exactly one host function tool is provided
  - either a BreadBoard tool-slice executor is explicitly supplied or the selected execution driver can execute directly
  - execution placement is negotiated through the execution-driver family
  - delegated remote execution may be selected when the host provides a remote execution adapter or endpoint
- two frozen driver-mediated expansions of that tool lane:
  - OCI-backed execution
  - delegated remote execution

## Explicitly unsupported in V1

These currently force native fallback or an unsupported-slice error:

- multimodal `images`
- `disableTools`
- block-reply callback surfaces
- richer channel / group routing fields
- inherited subagent policy surfaces
- tool-bearing embedded execution parity beyond the tracked single-tool fixture

## Bridge semantics

The bridge:

1. maps OpenClaw embedded-run params into `bb.run_request.v1`
2. routes provider-backed supported turns through `@breadboard/backbone`
3. routes tool-bearing supported turns through the execution-driver family
4. projects BreadBoard output into:
   - OpenClaw host callbacks
   - a minimal `EmbeddedPiRunResult`-compatible result
5. preserves an explicit native fallback seam

## Why this matters

This is the first proving-ground bridge that exercises a real host boundary against the stronger kernel contract program. It is intentionally narrow and should be treated as an executable seam proof, not as full OpenClaw compatibility.

In V3 terms, this is no longer only a bespoke bridge. It is the first concrete Host Kit realization built on top of the new backbone-facing product layer.

The supported provider-turn path now also uses the reusable Host Kit provider-session helper, which means transcript continuity on the BreadBoard path is no longer managed by OpenClaw-specific bridge code alone.

## Current stopping boundary

This program now stops at:

- host-boundary acceptance
- callback projection
- transcript continuity
- provider-quirk preservation
- delegated remote execution through the remote execution-driver boundary
- frozen OCI-backed and delegated-remote tool acceptance fixtures
- explicit native fallback

It does **not** currently stop at:

- full Pi replacement
- broad tool-rich embedded parity
- ACP parity
- delivery/channel parity

That boundary is intentional. It marks the end of the scoped TypeScript kernel and host-bridge effort, not a lack of future direction.
