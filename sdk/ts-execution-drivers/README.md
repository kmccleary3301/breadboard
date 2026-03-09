# @breadboard/execution-drivers

Shared execution-driver contracts and helper logic for TypeScript-side BreadBoard runtime work.

This package owns:
- execution-driver interface shape
- placement compatibility helpers
- unsupported-case helpers for execution-driver negotiation

This package does not own:
- kernel truth
- host bridge logic
- OCI runtime implementation details
- microVM lifecycle logic
