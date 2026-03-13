# Host Kit v1

## Purpose

Host Kit is the standard embedding abstraction for serious TS hosts.

OpenClaw proved the seam. V3 should generalize that seam into a reusable standard rather than continuing with one-off bridges.

## Expected responsibilities

A Host Kit should:
- classify support for a host request
- map host input into BreadBoard runtime entrypoints
- map kernel events into host callback or stream shapes
- build transcript continuation patches suitable for host-owned persistence
- make fallback decisions explicit
- surface support claims

## Relationship to Backbone

Backbone is the public TS runtime surface.
Host Kit is the host-specific adaptation layer above Backbone.

## Current state

The repo now has:
- a generalized `@breadboard/host-kits` package
- `createOpenClawHostKit(...)` as the first concrete Host Kit realization
- reusable support-claim normalization
- reusable supported/fallback invocation builders
- a reusable provider-turn session helper for transcript continuity and host-side projection state
- reusable transcript projection and callback-emission helpers for host-facing result shaping
- reusable host-facing result metadata helpers for bridge/package result envelopes

That means Host Kit is no longer just a design direction. It is now part of the public TS product layer, with OpenClaw serving as the first hard-runtime proving ground.

## Coordination projection boundary

Host Kits may project coordination outcomes outward, but they should treat them as downstream facts:

- accepted typed signals are kernel/orchestration truth
- wake messages shown to a host are projections
- a host may present blocked or completion state, but it should not invent mission authority rules on its own
