# Thin Host DX v1

## Purpose

Thin-host DX is the adoption surface for TypeScript hosts that do not want to own a large runtime seam.

Examples:
- T3 Code-class shells
- app-side chat panes
- AI SDK-backed UI shells
- lightweight internal coding assistants

## Product goal

The goal is to let a thin host adopt BreadBoard through:
- `@breadboard/backbone`
- `@breadboard/workspace`
- `@breadboard/transport-ai-sdk`
- a thin host starter package

without forcing the host to understand kernel substrate details directly.

## Responsibilities

A thin-host package should:
- classify a prompt turn through Backbone
- execute the supported provider-backed slice
- project the result into a UI-friendly transport
- surface SupportClaim and ExecutionProfile cleanly
- preserve transcript continuation when the host opts in

## Non-goals

Thin-host DX should not:
- redefine kernel event truth
- bypass Backbone to talk to lower substrate packages directly
- absorb host-specific persistence or routing concerns
- make unsupported runtime slices appear supported

## Current implementation direction

The first thin-host proving-ground package is `@breadboard/host-t3`.

Its purpose is not to prove runtime breadth. Its purpose is to prove that BreadBoard can feel easy, attractive, and unsurprising for a T3 Code-class host.

The current thin-host starter now includes:

- prompt-turn classification
- first-turn execution
- continuation-turn execution
- a reusable in-memory session wrapper for repeated prompt turns
- that session wrapper is now implemented through the reusable Host Kit provider-turn session helper
- resumable AI SDK-style transport projection
- explicit migration seeding through initial transcript and transport state in the session opener
