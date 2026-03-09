# @breadboard/host-kits

`@breadboard/host-kits` is the first reusable host-integration abstraction package for the V3 backbone program.

It standardizes:
- request classification
- support claims
- invocation results
- fallback-vs-supported mode reporting
- support-claim normalization
- reusable fallback/supported invocation builders
- reusable provider-turn session ownership for transcript continuity and host projection state
- reusable transcript/result projection helpers for host callbacks and host-shaped results
- reusable host-facing result metadata helpers
- reusable projection-state/result resolution helpers for session-oriented thin hosts

This package does not own host-specific request schemas by itself. It provides the reusable abstraction layer that concrete host kits, such as OpenClaw, should implement.
