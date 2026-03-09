# AI SDK Transport Adapter v1

## Purpose

The AI SDK transport adapter is a projection-only layer above the public Backbone API.

Its role is to make BreadBoard easy to integrate into AI SDK-style frontend and thin-host transport stacks without letting AI SDK abstractions become kernel truth.

## Current v1 slice

The current implementation slice is intentionally narrow:
- start frame
- resume frame
- assistant text delta frame
- tool preview frame
- transcript continuation patch frame
- finish frame

The adapter projects `BackboneTurnResult` values into transport frames suitable for AI SDK-style consumers.

## Non-goals

This adapter does not:
- redefine the kernel event model
- redefine transcript truth
- make AI SDK message state canonical
- subsume Host Kit responsibilities

## Relationship to the stack

- `@breadboard/backbone` owns the public runtime surface
- `@breadboard/transport-ai-sdk` owns a projection-only transport surface
- host apps remain free to ignore this package and consume raw Backbone results directly

## V3 widening direction

The adapter now also carries a small transport-state helper so thin hosts can:
- resume transport streams across continued turns
- consume transcript continuation patches without taking ownership of kernel semantics

This keeps AI SDK transport useful for adoption without letting it redefine transcript truth.
