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

It now also includes public product-layer helpers for:
- support-claim projection into a stable host-facing view
- terminal-registry projection
- terminal-session projection with public handles, support view, and end-state summaries
- terminal-end projection with shaped artifact/evidence refs
- terminal cleanup result construction through Backbone
- effective tool-surface projection through Backbone
- support-rich effective tool-surface analysis views, including:
  - visible entries
  - hidden entries
  - unsupported entries
  - fallback-selected bindings
  - resolution paths
- host-facing transcript/result envelopes that combine SupportClaim, projection state, and host-shaped result metadata
