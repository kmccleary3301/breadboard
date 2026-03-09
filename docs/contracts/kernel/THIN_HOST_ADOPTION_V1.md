# Thin Host Adoption V1

This document explains the current V3 adoption path for a thin TypeScript host such as a T3 Code-class app or an AI SDK-backed shell.

## BreadBoard owns

- provider-backed runtime turns through `@breadboard/backbone`
- workspace capability and execution-profile reasoning through `@breadboard/workspace`
- resumable transport projection through `@breadboard/transport-ai-sdk`
- thin-host starter ergonomics through `@breadboard/host-t3`

## The host still owns

- app routing
- persistence
- chat UI rendering
- auth/session UX
- any product-specific data model above the transport layer

## Recommended integration shape

1. Start with `createT3CodeStarter(...)`.
2. Use `classifyPromptTurn(...)` before running a new turn when the host needs to branch on support.
3. Use `runPromptTurn(...)` for the first provider-backed turn.
4. Use `continuePromptTurn(...)` or the session wrapper returned by `openSession(...)` for resumable flows.
5. Feed the returned transport frames to the host UI or AI SDK transport layer.

## Supported V3 slices

- prompt-turn classification
- provider-backed prompt turns
- transcript continuation
- resumable AI SDK-style transport projection
- public `SupportClaim` and `ExecutionProfile` surfaces for host decisions

## Explicit non-claims

V3 does not claim:

- full host persistence management
- hidden kernel ownership by the transport layer
- broad tool/runtime parity for every host
- app-specific UI state ownership

## Why this shape is attractive

- a thin TS app can adopt BreadBoard without touching kernel substrate directly
- transport projection stays projection-only
- the host gets explicit support and execution-profile signals
- the migration path is incremental instead of all-or-nothing

