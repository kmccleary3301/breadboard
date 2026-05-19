# @breadboard/host-t3

`@breadboard/host-t3` is a small TypeScript host-boundary experiment package.

It is intentionally small:
- compose `@breadboard/backbone`
- compose `@breadboard/workspace`
- compose `@breadboard/transport-ai-sdk`
- expose a one-package prompt-turn integration surface for local experiments

Current scope:
- supported provider-backed prompt turns
- AI SDK-style transport projection
- transcript-continuation-aware prompt execution
- resumable continuation turns with prior transport state
- a reusable thin-host session wrapper for repeated prompt turns
- host-session state managed through the reusable `@breadboard/host-kits` provider-turn session helper

This package is intentionally narrow. It should be evaluated by its shipped examples and tests, not as a public host-adoption target.

Example:
- [`examples/basic-session.ts`](./examples/basic-session.ts)
- [`examples/migration-starter.ts`](./examples/migration-starter.ts)
- [`examples/next-route-handler.ts`](./examples/next-route-handler.ts)
- [`examples/session-store.ts`](./examples/session-store.ts)

Integration posture:
- keep persistence and routing host-owned
- optionally seed transcript and transport state into `openSession(...)`
- let BreadBoard own turn execution, transcript continuation, and projection state updates

Suggested evaluation order:
1. Start with `createT3CodeStarter(...)` for a route-level smoke.
2. Move to `openSession(...)` when the host already owns transcript and transport persistence.
3. Use the examples as bounded integration references, not as framework lock-in.
