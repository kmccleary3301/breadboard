# OpenClaw Host Bridge Acceptance V1

This document records the first frozen acceptance slice for the OpenClaw embedded-runner bridge.

In V3, these fixtures also serve as the first acceptance base for the OpenClaw Host Kit realization built on top of Backbone rather than bridge-local provider-turn orchestration.

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
- `sdk/ts-host-bridges/test/fixtures/openclaw_embedded_transcript_continuation_slice.json`
- `sdk/ts-host-bridges/test/fixtures/openclaw_embedded_tool_slice.json`
- `sdk/ts-host-bridges/test/fixtures/openclaw_embedded_provider_quirk_slice.json`
- `sdk/ts-host-bridges/test/fixtures/openclaw_embedded_oci_tool_slice.json`
- `sdk/ts-host-bridges/test/fixtures/openclaw_embedded_remote_tool_slice.json`

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

## Transcript continuity note

The second frozen fixture captures the more realistic host-level requirement:

- preserve host-owned transcript pre-state
- append the current turn through BreadBoard
- return a transcript post-state suitable for host-owned persistence

That is still not claiming ownership of OpenClaw session storage. It is proving that BreadBoard can participate in transcript continuity without taking over transcript persistence.

## Tool-bearing note

The third frozen fixture captures the first honest tool-bearing embedded slice:

- exactly one host-provided function tool
- explicit execution-driver planning
- explicit sandbox request/result boundary
- callback projection for tool results and assistant text
- no claim of broad Pi tool parity

This is intentionally narrow. It proves the host boundary can carry a driver-mediated tool run without pretending the whole Pi runtime has been replaced.

The current implementation also supports two practical expansions of that slice:

- direct trusted-local execution through the local execution driver
- OCI-backed execution through the OCI driver when the host provides an OCI runtime adapter
- delegated remote execution through the remote execution driver when the host provides a remote adapter or endpoint

Those are implementation-backed capabilities, and the OCI-backed and delegated-remote lanes are now frozen as explicit acceptance fixtures, but they are still governed by the same intentionally narrow supported-slice rules.

## Provider-quirk note

The fourth frozen fixture captures the first provider-quirk preservation lane:

- host request carries richer provider-routing intent
- BreadBoard path preserves provider family, runtime id, and route id
- response-side finish reason survives the bridge projection
- no claim is made that every Pi/OpenClaw provider quirk is now supported

This slice matters because a credible host bridge cannot collapse all provider behavior into a generic text path. It must preserve the subset of provider-routing semantics that the host actually depends on.

## Explicit closure condition for this program

For the current TypeScript SDK effort, the OpenClaw proving-ground is considered complete when:

1. the supported embedded-run slice is frozen
2. the transcript-continuation slice is frozen
3. the narrow tool-bearing slice is frozen
4. the provider-quirk preservation slice is frozen
5. fallback behavior is explicit and tested
6. the bridge does not overclaim tool-rich or ACP parity

That condition is now met.
