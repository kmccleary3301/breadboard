# T3 Backbone Adoption V1

This document gives the current recommended adoption path for a thin TypeScript host that wants to use BreadBoard as its backbone without inheriting kernel-level complexity.

## Recommended path

1. Start with `@breadboard/host-t3`.
2. Keep routing, persistence, auth UX, and UI rendering in the host app.
3. Let BreadBoard own:
   - provider-turn classification
   - provider-turn execution
   - transcript continuation
   - resumable transport projection
4. Seed existing transcript and transport state into `openSession(...)` when migrating an existing host session surface.

## Suggested migration stages

### Stage 1: Route-level proof

Use the starter directly in a route handler:

- [`sdk/ts-host-t3/examples/next-route-handler.ts`](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/sdk/ts-host-t3/examples/next-route-handler.ts)

This is the fastest way to prove:
- the host can call BreadBoard without touching kernel substrate
- the host can receive resumable transport frames
- the host can preserve transcript continuity

### Stage 2: Host-owned session persistence

Move to the session wrapper and seed prior state explicitly:

- [`sdk/ts-host-t3/examples/session-store.ts`](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/sdk/ts-host-t3/examples/session-store.ts)

This preserves the clean ownership split:
- BreadBoard owns turn semantics
- the host owns persistence and routing

### Stage 3: Thin-host productization

Once the route/session proof is stable:
- integrate the returned transport frames into the app’s chat surface
- use `SupportClaim` to branch UI or fallback decisions
- carry transcript and transport state in the host’s normal persistence model

## Non-goals

This thin-host path does not claim:
- host persistence ownership by BreadBoard
- UI ownership by BreadBoard
- broad tool-rich runtime parity for every TS host
- transport as kernel truth

## Why this is the right shape

This path is attractive because it is:
- incremental
- host-owned where it should be
- projection-driven rather than substrate-driven
- maintainable for app teams that do not want to reason about kernel internals
