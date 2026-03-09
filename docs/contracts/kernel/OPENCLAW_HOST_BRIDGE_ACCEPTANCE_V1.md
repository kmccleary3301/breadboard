# OpenClaw Host Bridge Acceptance V1

This document records the first frozen acceptance slice for the OpenClaw embedded-runner bridge.

## Selected slice

The current frozen slice is the narrow supported embedded-run path:

- single prompt
- single provider/model lane
- no multimodal inputs
- no client-provided tools
- no block-reply path
- no group/channel routing specialization

## Why this slice

It is the smallest OpenClaw-shaped request that still proves the important boundary:

1. OpenClaw host params are accepted
2. they are mapped into BreadBoard kernel contracts
3. BreadBoard kernel events are projected back into OpenClaw callbacks
4. the host receives an `EmbeddedPiRunResult`-compatible terminal result
5. unsupported cases still have a clean native fallback seam

## Frozen artifacts

Tracked acceptance fixture:

- `sdk/ts-host-bridges/test/fixtures/openclaw_embedded_supported_slice.json`

Tracked executable proof:

- `sdk/ts-host-bridges/test/openclaw.test.ts`

## Current limits

This acceptance slice does **not** claim:

- transcript persistence parity
- ACP parity
- tool-rich embedded execution parity
- multimodal parity
- channel-delivery parity
- full Pi runtime replacement

It is a proving-ground contract for the bridge seam, not a full OpenClaw compatibility claim.
