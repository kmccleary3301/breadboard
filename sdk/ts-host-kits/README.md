# @breadboard/host-kits

`@breadboard/host-kits` is a reusable host-integration abstraction package for TypeScript runtime-boundary experiments.

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

This package does not own host-specific request schemas by itself. It provides reusable abstraction helpers for concrete bridge packages.

It also includes helpers for:
- support-claim projection into a stable host-facing view
- terminal-support projection into a stable host-facing capability summary
- terminal-registry projection
- terminal-session projection with public handles, support view, and end-state summaries
- terminal-interaction projection with shaped output and optional end-state summary
- terminal-end projection with shaped artifact/evidence refs
- terminal cleanup result construction through Backbone
- effective tool-surface projection through Backbone
- support-rich effective tool-surface analysis views, including:
  - visible entries
  - hidden entries
  - unsupported entries
  - fallback-selected bindings
  - resolution paths
- compact effective tool-surface support summaries for host/product UIs that only need counts and fallback visibility
- host-facing transcript/result envelopes that combine SupportClaim, projection state, and host-shaped result metadata
