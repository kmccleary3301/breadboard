# @breadboard/host-t3

`@breadboard/host-t3` is the first thin-host DX proving-ground package for the V3 backbone program.

It is intentionally small:
- compose `@breadboard/backbone`
- compose `@breadboard/workspace`
- compose `@breadboard/transport-ai-sdk`
- give T3 Code-class hosts a one-package prompt-turn integration surface

Current scope:
- supported provider-backed prompt turns
- AI SDK-style transport projection
- transcript-continuation-aware prompt execution
- resumable continuation turns with prior transport state
- a reusable thin-host session wrapper for repeated prompt turns
- host-session state managed through the reusable `@breadboard/host-kits` provider-turn session helper

This package exists to prove that BreadBoard can be attractive for thin TypeScript hosts without exposing kernel substrate details directly.

Example:
- [`examples/basic-session.ts`](./examples/basic-session.ts)
- [`examples/migration-starter.ts`](./examples/migration-starter.ts)

Migration posture:
- keep persistence and routing host-owned
- optionally seed transcript and transport state into `openSession(...)`
- let BreadBoard own turn execution, transcript continuation, and projection state updates
