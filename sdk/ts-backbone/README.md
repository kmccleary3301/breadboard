# @breadboard/backbone

`@breadboard/backbone` is the first public V3 product layer above the kernel.

It exposes host-shaped objects:
- `createBackbone(...)`
- `BackboneSession`
- `SupportClaim`
- `ProjectionProfile`

This package is intentionally above `@breadboard/kernel-core` and below host-specific kits.

Current scope:
- session open/classification
- provider-backed text turns
- driver-mediated tool turns
- support-claim generation
- projection-profile exposure

It does **not** replace Host Kits or Workspace. It composes them.
