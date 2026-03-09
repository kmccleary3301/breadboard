# @breadboard/transport-ai-sdk

`@breadboard/transport-ai-sdk` is a projection-only adapter package for V3.

It does not make AI SDK into kernel truth.
It projects `BackboneTurnResult` values into a small AI SDK-style transport frame stream that host apps can reuse.

Current scope:
- start frame
- resume frame for transcript-continuation turns
- assistant text delta frame
- tool preview frame
- transcript-continuation patch frame
- finish frame

It also exposes a small transport-state helper so thin hosts can resume the streamable projection layer without owning kernel semantics.

Current public helpers:
- `projectBackboneTurnToAiSdkTransport(...)`
- `deriveAiSdkTransportState(...)`
- `createAiSdkTransportSession(...)`
- `AiSdkTransportSession.appendTurn(...)` for resumable auto-projection

The session helper exists for thin hosts that want resumable projection state without turning the transport layer into kernel truth.

This is intentionally narrow and designed to sit above the Backbone API.
