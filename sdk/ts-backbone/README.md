# @breadboard/backbone

`@breadboard/backbone` is a TypeScript runtime-boundary package above the kernel.

It exposes host-shaped objects:
- `createBackbone(...)`
- `BackboneSession`
- `SupportClaim`
- `ProjectionProfile`
- `ExecutionProfile` via the workspace-backed product surface

This package is intentionally above `@breadboard/kernel-core` and below host-specific kits.

Current scope:
- session open/classification
- provider-backed text turns
- driver-mediated tool turns
- support-claim generation
- execution-profile-aware support classification
- projection-profile exposure
- terminal session lifecycle, registry reduction, session lookup/listing, and cleanup result construction through `BackboneSession.terminals`
- effective tool-surface construction through `BackboneSession.tools`

Consumers should treat `SupportClaim` as the package-level truth for:
- whether a slice is supported
- which execution profile is in play
- how confident BreadBoard is about the supported path
- what host interaction mode is recommended

Hosts should treat:
- `BackboneSession.terminals`
  as the package-level entrypoint for persistent terminal session lifecycle, registry/listing views, and cleanup result construction
- `BackboneSession.tools`
  as the package-level entrypoint for effective tool-surface construction

It does **not** replace Host Kits or Workspace. It composes them.
