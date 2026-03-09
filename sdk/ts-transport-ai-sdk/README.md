# @breadboard/transport-ai-sdk

`@breadboard/transport-ai-sdk` is a projection-only adapter package for V3.

It does not make AI SDK into kernel truth.
It projects `BackboneTurnResult` values into a small AI SDK-style transport frame stream that host apps can reuse.

Current scope:
- start frame
- assistant text delta frame
- tool preview frame
- finish frame

This is intentionally narrow and designed to sit above the Backbone API.
