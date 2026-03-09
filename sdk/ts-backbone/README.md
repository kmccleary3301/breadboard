# @breadboard/backbone

`@breadboard/backbone` is the first public V3 product layer above the kernel.

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

Hosts should treat `SupportClaim` as the public truth for:
- whether a slice is supported
- which execution profile is in play
- how confident BreadBoard is about the supported path
- what host interaction mode is recommended

It does **not** replace Host Kits or Workspace. It composes them.
